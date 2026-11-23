"""
MT5 bridge provider: prices pushed by `bridge/mt5_bridge.py`, a small script that runs next to the user's own
MetaTrader 5 terminal (a broker demo works) and sends its latest bid/ask and closed 1-minute candles to
`POST /api/bridge/push`. This provider is market data only; orders are a separate, opt-in path
(EXECUTION_MODE=MT5_DEMO or MT5_LIVE, app/execution/mt5_executor.py).

  MARKET_DATA_PROVIDER=BRIDGE      use it for the paper loop
  SPARKZ_BRIDGE_TOKEN=<secret>     shared secret; the push endpoint refuses every request without it

Candles of the strategy timeframe are built from the pushed 1m candles (labelled by open time; only candles
whose whole period has closed are returned). The market counts as closed when no tick has arrived for
`stale_seconds` (terminal closed, broker offline, weekend).

Spread report (`GET /api/bridge/spread`): the broker's real spread, from every distinct live tick (ask - bid)
and from the spread MT5 stores on each 1-minute candle (sent with the history, so numbers exist before the
market opens), overall and by session, next to the spread the backtests assume.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np

import pandas as pd

from app.config import TIMEFRAMES
from app.market.instruments import get_instrument
from app.market.providers.base import MarketDataProvider, MarketStatus, ProviderError, ProviderInfo, Tick

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
SESSIONS = (("ASIA", 0, 7), ("LONDON", 7, 12), ("NEW_YORK", 12, 21), ("LATE", 21, 24))     # UTC hours


def spread_stats(values) -> dict:
    a = np.asarray(values, float)
    if not len(a):
        return {"count": 0}
    return {"count": int(len(a)), "median": round(float(np.median(a)), 3), "mean": round(float(a.mean()), 3),
            "p90": round(float(np.quantile(a, 0.9)), 3), "p99": round(float(np.quantile(a, 0.99)), 3),
            "min": round(float(a.min()), 3), "max": round(float(a.max()), 3)}


def by_session(times, values) -> dict:
    hours = np.array([t.hour for t in times])
    v = np.asarray(values, float)
    return {name: spread_stats(v[(hours >= a) & (hours < b)]) for name, a, b in SESSIONS}


class BridgeStore:
    """What the bridge has pushed, shared by the API (writer) and the provider (reader)."""

    def __init__(self, max_bars: int = 100_000, clock=time.time):
        self.lock = threading.Lock()
        self.max_bars, self.clock = max_bars, clock
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.symbol: str | None = None
            self.broker_symbol: str | None = None
            self.tick: Tick | None = None
            self.tick_received: float | None = None
            self.bars: dict[pd.Timestamp, tuple] = {}
            self.bar_spreads: dict[pd.Timestamp, float] = {}
            self.htf: dict[str, dict[pd.Timestamp, tuple]] = {}   # native 1h / 4h / 1d candles from MT5
            self.tick_spreads: deque = deque(maxlen=200_000)     # (time, spread) of distinct ticks
            self.pushes = 0

    def push(self, symbol: str, broker_symbol: str, tick: Tick | None, bars: list[dict],
             htf: dict[str, list[dict]] | None = None) -> dict:
        with self.lock:
            self.symbol, self.broker_symbol = symbol, broker_symbol
            if tick is not None:
                if self.tick is None or tick.time != self.tick.time:   # the bridge repeats an unchanged quote
                    self.tick_spreads.append((tick.time, tick.spread))
                self.tick, self.tick_received = tick, self.clock()
            for b in bars:
                ts = pd.Timestamp(b["time"]).tz_convert("UTC")
                self.bars[ts] = (float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]),
                                 float(b.get("volume", 0.0)))
                if b.get("spread") is not None:
                    self.bar_spreads[ts] = float(b["spread"])
            for tf, rows in (htf or {}).items():
                store = self.htf.setdefault(tf, {})
                for b in rows:
                    store[pd.Timestamp(b["time"]).tz_convert("UTC")] = (
                        float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]), float(b.get("volume", 0.0)))
            if len(self.bars) > self.max_bars:
                for k in sorted(self.bars)[: len(self.bars) - self.max_bars]:
                    del self.bars[k]
                    self.bar_spreads.pop(k, None)
            self.pushes += 1
            return self.status()

    def status(self) -> dict:
        last = max(self.bars) if self.bars else None
        return {"symbol": self.symbol, "broker_symbol": self.broker_symbol, "pushes": self.pushes,
                "bars_1m": len(self.bars), "last_bar": last.isoformat() if last is not None else None,
                "last_tick": self.tick.time.isoformat() if self.tick else None,
                "higher_timeframes": {tf: len(v) for tf, v in self.htf.items()},
                "tick_age_seconds": None if self.tick_received is None else round(self.clock() - self.tick_received, 1)}

    def spread_report(self, assumed: float | None = None) -> dict:
        with self.lock:
            ticks = list(self.tick_spreads)
            bars = sorted(self.bar_spreads.items())
        out = {"symbol": self.symbol, "broker_symbol": self.broker_symbol, "backtest_assumption": assumed,
               "live_ticks": {**spread_stats([s for _, s in ticks]),
                              "first": ticks[0][0].isoformat() if ticks else None,
                              "last": ticks[-1][0].isoformat() if ticks else None,
                              "by_session_utc": by_session([t for t, _ in ticks], [s for _, s in ticks])},
               "mt5_1m_candles": {**spread_stats([s for _, s in bars]),
                                  "first": bars[0][0].isoformat() if bars else None,
                                  "last": bars[-1][0].isoformat() if bars else None,
                                  "by_session_utc": by_session([t for t, _ in bars], [s for _, s in bars]),
                                  "note": "the spread MetaTrader 5 records on each 1-minute candle"}}
        med = out["live_ticks"].get("median") or out["mt5_1m_candles"].get("median")
        if med is not None and assumed:
            out["vs_backtest"] = f"median spread {med} is {med / assumed:.1f}x the backtest's {assumed}"
        return out

    def frame(self, tf: str | None = None) -> pd.DataFrame:
        """1m candles, or with `tf` the native higher-timeframe candles MT5 sent."""
        with self.lock:
            items = sorted((self.htf.get(tf, {}) if tf else self.bars).items())
        if not items:
            return pd.DataFrame(columns=COLUMNS)
        df = pd.DataFrame([(k, *v) for k, v in items], columns=COLUMNS)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df


STORE = BridgeStore()


class BridgeProvider(MarketDataProvider):
    def __init__(self, symbol: str = "XAUUSD", timeframe: str = "15m", store: BridgeStore | None = None,
                 stale_seconds: float = 120.0):
        self.instrument = get_instrument(symbol)
        self.timeframe = timeframe
        self.store = store or STORE
        self.stale_seconds = stale_seconds

    def info(self) -> ProviderInfo:
        bs = self.store.broker_symbol
        return ProviderInfo("MT5 bridge" + (f" ({bs})" if bs else ""), "BRIDGE", live=True,
                            notes=["prices from the user's MetaTrader 5 terminal via bridge/mt5_bridge.py",
                                   "prices only; orders go to MT5 only with EXECUTION_MODE=MT5_DEMO or MT5_LIVE and a bridge run with --trade"])

    def _check_symbol(self) -> None:
        if self.store.symbol and self.store.symbol != self.instrument.symbol:
            raise ProviderError(f"bridge is pushing {self.store.symbol}, the robot trades {self.instrument.symbol}")

    def get_latest_tick(self) -> Tick:
        self._check_symbol()
        if self.store.tick is None:
            raise ProviderError("no price from the MT5 bridge yet")
        return self.store.tick

    def get_candles(self, timeframe: str, count: int) -> pd.DataFrame:
        self._check_symbol()
        m1 = self.store.frame()
        native = self.store.frame(timeframe) if timeframe in ("1h", "4h", "1d") else pd.DataFrame(columns=COLUMNS)
        if not len(m1):
            return native[COLUMNS].tail(count).reset_index(drop=True)
        step = TIMEFRAMES[timeframe]
        closed_until = m1["timestamp"].iloc[-1] + pd.Timedelta(minutes=1)      # the last pushed 1m bar is closed
        if step == 60:
            out = m1
        else:
            out = m1.set_index("timestamp").resample(f"{step}s", label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna(subset=["open"]).reset_index()
            out = out[out["timestamp"] + pd.Timedelta(seconds=step) <= closed_until]
            if len(native):                  # MT5's own candles, then the newer ones built from 1m candles
                out = pd.concat([native, out[out["timestamp"] > native["timestamp"].iloc[-1]]], ignore_index=True)
        return out[COLUMNS].tail(count).reset_index(drop=True)

    def get_market_status(self) -> MarketStatus:
        now = datetime.now(timezone.utc)
        rec = self.store.tick_received
        if rec is None:
            return MarketStatus(False, "waiting for the MT5 bridge", now)
        age = self.store.clock() - rec
        if age > self.stale_seconds:
            return MarketStatus(False, f"no tick from the MT5 bridge for {age:.0f} s", now)
        return MarketStatus(True, "", now)
