"""
Per-timeframe market context: everything the strategy detectors read, computed once per candle series.

Every column at row i depends only on candles 0..i (indicators are backward-looking; a swing point is used
only from the candle that confirms it, `n` candles later), so evaluating a detector at row i never sees the
future, whether i is the latest candle (live) or a past one (history, studies).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.market.indicators import compute_features, ema
from app.research.structure import asia_range, pivots, structure, sweeps

TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
SWING_N = {"1m": 3, "5m": 3, "15m": 3, "1h": 3, "4h": 2, "1d": 2}


@dataclass
class Context:
    tf: str
    minutes: int
    f: pd.DataFrame            # candles + indicators (compute_features) + ema9/ema21
    st: pd.DataFrame           # structure: sh, sl, trend, event
    sw: pd.DataFrame           # sweep_low / sweep_high
    ph_conf: np.ndarray        # index of each swing high, at the row that confirms it (-1 otherwise)
    pl_conf: np.ndarray        # same for swing lows
    n: int                     # swing size

    def __len__(self) -> int:
        return len(self.f)

    def row(self, i: int):
        return self.f.iloc[i]

    def swing_highs(self, i: int, lookback: int) -> list[tuple[int, float]]:
        """Confirmed swing highs known at row i, within the last `lookback` rows: (pivot row, price)."""
        lo = max(0, i - lookback)
        idx = self.ph_conf[lo:i + 1]
        piv = idx[idx >= 0]
        return [(int(k), float(self.f["high"].iat[k])) for k in piv]

    def swing_lows(self, i: int, lookback: int) -> list[tuple[int, float]]:
        lo = max(0, i - lookback)
        idx = self.pl_conf[lo:i + 1]
        piv = idx[idx >= 0]
        return [(int(k), float(self.f["low"].iat[k])) for k in piv]


def build(candles: pd.DataFrame, tf: str) -> Context:
    df = candles[["timestamp", "open", "high", "low", "close"]].copy().reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if "volume" in candles:
        df["volume"] = candles["volume"].to_numpy()
    f = compute_features(df)
    f["ema9"], f["ema21"] = ema(f["close"], 9), ema(f["close"], 21)
    f["hi20"] = f["high"].rolling(20, min_periods=5).max().shift(1)     # range of the 20 candles BEFORE this one
    f["lo20"] = f["low"].rolling(20, min_periods=5).min().shift(1)
    f["atr_mean100"] = f["atr"].rolling(100, min_periods=20).mean()
    n = SWING_N.get(tf, 3)
    st = structure(df, n)
    sw = sweeps(df, st, asia_range(df) if TF_MINUTES.get(tf, 60) < 60 else None)
    ph, pl = pivots(df["high"].to_numpy(float), df["low"].to_numpy(float), n)
    m = len(df)
    ph_conf, pl_conf = np.full(m, -1), np.full(m, -1)
    hi_idx, lo_idx = np.flatnonzero(ph), np.flatnonzero(pl)
    ph_conf[hi_idx[hi_idx + n < m] + n] = hi_idx[hi_idx + n < m]
    pl_conf[lo_idx[lo_idx + n < m] + n] = lo_idx[lo_idx + n < m]
    return Context(tf, TF_MINUTES.get(tf, 60), f, st, sw, ph_conf, pl_conf, n)


def resample(candles: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Finer candles -> `tf` candles (labelled by open time). The last bin may still be forming: callers that
    need closed candles only should pass closed input and drop a partial final bin themselves."""
    rule = f"{TF_MINUTES[tf]}min"
    df = candles.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df:
        agg["volume"] = "sum"
    return df.set_index("timestamp").resample(rule, label="left", closed="left").agg(agg).dropna(
        subset=["open"]).reset_index()
