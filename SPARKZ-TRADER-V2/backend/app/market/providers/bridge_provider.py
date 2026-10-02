"""
MT5 bridge provider: prices pushed by `bridge/mt5_bridge.py`, a small script that runs next to the user's own
MetaTrader 5 terminal (a broker demo works) and sends its latest bid/ask and closed 1-minute candles to
`POST /api/bridge/push`. MARKET DATA ONLY: the bridge reads quotes, nothing here can reach an order.

  MARKET_DATA_PROVIDER=BRIDGE      use it for the paper loop
  SPARKZ_BRIDGE_TOKEN=<secret>     shared secret; the push endpoint refuses every request without it

Candles of the strategy timeframe are built from the pushed 1m candles (labelled by open time; only candles
whose whole period has closed are returned). The market counts as closed when no tick has arrived for
`stale_seconds` (terminal closed, broker offline, weekend).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pandas as pd

from app.config import TIMEFRAMES
from app.market.instruments import get_instrument
from app.market.providers.base import MarketDataProvider, MarketStatus, ProviderError, ProviderInfo, Tick

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


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
            self.pushes = 0

    def push(self, symbol: str, broker_symbol: str, tick: Tick | None, bars: list[dict]) -> dict:
        with self.lock:
            self.symbol, self.broker_symbol = symbol, broker_symbol
            if tick is not None:
                self.tick, self.tick_received = tick, self.clock()
            for b in bars:
                ts = pd.Timestamp(b["time"]).tz_convert("UTC")
                self.bars[ts] = (float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]),
                                 float(b.get("volume", 0.0)))
            if len(self.bars) > self.max_bars:
                for k in sorted(self.bars)[: len(self.bars) - self.max_bars]:
                    del self.bars[k]
            self.pushes += 1
            return self.status()

    def status(self) -> dict:
        last = max(self.bars) if self.bars else None
        return {"symbol": self.symbol, "broker_symbol": self.broker_symbol, "pushes": self.pushes,
                "bars_1m": len(self.bars), "last_bar": last.isoformat() if last is not None else None,
                "last_tick": self.tick.time.isoformat() if self.tick else None,
                "tick_age_seconds": None if self.tick_received is None else round(self.clock() - self.tick_received, 1)}

    def frame(self) -> pd.DataFrame:
        with self.lock:
            items = sorted(self.bars.items())
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
                                   "market data only: no order is ever sent"])

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
        if not len(m1):
            return m1
        step = TIMEFRAMES[timeframe]
        closed_until = m1["timestamp"].iloc[-1] + pd.Timedelta(minutes=1)      # the last pushed 1m bar is closed
        if step == 60:
            out = m1
        else:
            out = m1.set_index("timestamp").resample(f"{step}s", label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna(subset=["open"]).reset_index()
            out = out[out["timestamp"] + pd.Timedelta(seconds=step) <= closed_until]
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
