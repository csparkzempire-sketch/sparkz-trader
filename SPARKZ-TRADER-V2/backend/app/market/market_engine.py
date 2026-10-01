"""
Market engine: turns provider data into MarketState snapshots.

Two kinds of update:
- on_candle_close(candle): a candle has CLOSED. Indicators and the regime are
  recomputed and a new "bar" MarketState is produced. Strategy analysis and
  entries only ever look at bar states.
- on_tick(tick): a new bid/ask. The live state keeps the last bar's indicators
  and updates bid, ask, mid and spread. Grid adds, targets and stops react to ticks.

No future data: a bar state for candle N is built from candles 0..N only. In a
backtest the features are computed once over the whole series (every feature
is causal, so row N is identical to computing on candles 0..N; tests check
this). The paper engine recomputes over its rolling window of
`market.history_bars` candles; EMAs started from a 600-bar window differ from
full-history EMAs by a negligible amount, measured in tests/test_market.py.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime

import pandas as pd

from app.config import Settings
from app.market.indicators import compute_features
from app.market.instruments import get_instrument
from app.market.providers.base import Tick
from app.market.regime import label_regimes


@dataclass(frozen=True)
class MarketState:
    symbol: str
    timestamp: datetime          # bar states: the candle's open time; live states: the tick time
    bar_time: datetime | None    # open time of the last closed candle behind the indicators
    bid: float
    ask: float
    mid: float
    spread: float
    open: float
    high: float
    low: float
    close: float
    atr: float
    rsi: float
    ema20: float
    ema50: float
    ema200: float
    macd: float
    macd_signal: float
    macd_hist: float
    adx: float
    plus_di: float
    minus_di: float
    bb_upper: float
    bb_lower: float
    bb_mid: float
    volatility: float            # realized volatility (std of log returns, 20 bars)
    vol_percentile: float
    ret_recent: float
    regime: str
    trend_regime: str
    vol_regime: str
    ready: bool
    kind: str = "bar"            # "bar" or "live"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat() if self.timestamp else None
        d["bar_time"] = self.bar_time.isoformat() if self.bar_time else None
        return {k: (None if isinstance(v, float) and v != v else v) for k, v in d.items()}


def featurize(candles: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    return label_regimes(compute_features(candles, settings.analysis), settings.analysis)


def _f(row, key) -> float:
    v = row[key]
    return float(v) if pd.notna(v) else float("nan")


def state_from_row(row: pd.Series, symbol: str, spread: float) -> MarketState:
    close = float(row["close"])
    t = pd.Timestamp(row["timestamp"]).to_pydatetime()
    return MarketState(
        symbol=symbol, timestamp=t, bar_time=t, bid=close - spread / 2, ask=close + spread / 2, mid=close,
        spread=spread, open=float(row["open"]), high=float(row["high"]), low=float(row["low"]), close=close,
        atr=_f(row, "atr"), rsi=_f(row, "rsi"), ema20=_f(row, "ema_fast"), ema50=_f(row, "ema_slow"),
        ema200=_f(row, "ema_trend"), macd=_f(row, "macd"), macd_signal=_f(row, "macd_signal"),
        macd_hist=_f(row, "macd_hist"), adx=_f(row, "adx"), plus_di=_f(row, "plus_di"),
        minus_di=_f(row, "minus_di"), bb_upper=_f(row, "bb_upper"), bb_lower=_f(row, "bb_lower"),
        bb_mid=_f(row, "bb_mid"), volatility=_f(row, "realized_vol"), vol_percentile=_f(row, "vol_percentile"),
        ret_recent=_f(row, "ret_recent"), regime=str(row["regime"]), trend_regime=str(row["trend_regime"]),
        vol_regime=str(row["vol_regime"]), ready=str(row["regime"]) != "WARMUP")


class MarketEngine:
    def __init__(self, settings: Settings, precomputed: pd.DataFrame | None = None, keep: int = 500):
        self.s = settings
        self.inst = get_instrument(settings.market.symbol)
        self.candles = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        self._pre = precomputed              # backtest: features for the whole series, indexed by bar
        self.bar_states: deque[MarketState] = deque(maxlen=keep)
        self.last_bar: MarketState | None = None
        self.live: MarketState | None = None
        self.last_tick: Tick | None = None

    # ---- paper: rolling window --------------------------------------------------------------
    def load_history(self, candles: pd.DataFrame) -> MarketState | None:
        self.candles = candles.tail(self.s.market.history_bars).reset_index(drop=True)
        if self.candles.empty:
            return None
        return self._recompute()

    def on_candle_close(self, candle: pd.Series | dict, index: int | None = None) -> MarketState:
        """A candle closed. Backtest passes `index` into the precomputed features."""
        if self._pre is not None and index is not None:
            st = state_from_row(self._pre.iloc[index], self.inst.symbol, self._spread())
        else:
            row = pd.DataFrame([dict(candle)])
            row["timestamp"] = pd.to_datetime(row["timestamp"], utc=True)
            if len(self.candles) and row["timestamp"].iloc[0] <= self.candles["timestamp"].iloc[-1]:
                return self.last_bar  # already processed
            self.candles = pd.concat([self.candles, row], ignore_index=True).tail(self.s.market.history_bars) \
                .reset_index(drop=True)
            st = self._recompute()
        self.last_bar = st
        self.bar_states.append(st)
        return st

    def _recompute(self) -> MarketState:
        feats = featurize(self.candles.astype({c: float for c in ("open", "high", "low", "close")}), self.s)
        st = state_from_row(feats.iloc[-1], self.inst.symbol, self._spread())
        self.last_bar = st
        return st

    def _spread(self) -> float:
        e = self.s.execution
        base = e.spread_override if e.spread_override is not None else (
            self.last_tick.spread if self.last_tick and self.last_tick.spread > 0 else self.inst.spread)
        return base * e.spread_multiplier

    def on_tick(self, tick: Tick) -> MarketState | None:
        self.last_tick = tick
        if self.last_bar is None:
            return None
        sp = self._spread()
        self.live = replace(self.last_bar, timestamp=tick.time, bid=tick.mid - sp / 2, ask=tick.mid + sp / 2,
                            mid=tick.mid, spread=sp, kind="live")
        return self.live
