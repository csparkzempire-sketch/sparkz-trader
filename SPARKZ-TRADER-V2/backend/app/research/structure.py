"""
Market structure from OHLC candles, without look-ahead.

  swing high / low   a pivot: the bar's high (low) is the extreme of the `n` bars on each side. A pivot
                     at bar i is only KNOWN at bar i + n (its right-hand bars must have closed), so every
                     column below uses a pivot from the bar where it is confirmed onwards, never earlier.
  trend              +1 bullish / -1 bearish / 0 undecided, changed only by a candle CLOSE beyond the last
                     confirmed swing:
  BOS                break of structure: a close beyond the last swing in the direction of the trend
                     (continuation)
  CHoCH              change of character: the first close beyond the last swing AGAINST the trend
                     (the trend flips)
  sessions           Asia 00:00-07:00, London 07:00-12:00, New York 12:00-21:00 (UTC); the Asia range of
                     the current day is known from 07:00.
  liquidity sweep    a wick through a level (last swing low/high or the Asia low/high) with the close back
                     on the other side: stops below the level were taken, the move did not continue.

Every column at row t depends only on candles 0..t (tests check that truncating the series changes nothing).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SESSIONS = {"ASIA": (0, 7), "LONDON": (7, 12), "NEW_YORK": (12, 21)}


def pivots(high: np.ndarray, low: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Boolean arrays: bar i is a swing high / swing low (strictly above / below the n bars before it,
    at least as high / low as the n bars after it)."""
    m = len(high)
    ph, pl = np.zeros(m, bool), np.zeros(m, bool)
    if m < 2 * n + 1:
        return ph, pl
    win = 2 * n + 1
    hv = np.lib.stride_tricks.sliding_window_view(high, win)
    lv = np.lib.stride_tricks.sliding_window_view(low, win)
    centre_h, centre_l = hv[:, n], lv[:, n]
    ph[n:m - n] = (centre_h > hv[:, :n].max(axis=1)) & (centre_h >= hv[:, n + 1:].max(axis=1))
    pl[n:m - n] = (centre_l < lv[:, :n].min(axis=1)) & (centre_l <= lv[:, n + 1:].min(axis=1))
    return ph, pl


def structure(df: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    """Per candle: last confirmed swing high/low (sh, sl), trend and the structure event of that candle
    (BOS_UP, BOS_DOWN, CHOCH_UP, CHOCH_DOWN or "")."""
    high, low, close = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    ph, pl = pivots(high, low, n)
    m = len(df)
    sh_out, sl_out = np.full(m, np.nan), np.full(m, np.nan)
    trend_out = np.zeros(m, np.int8)
    event = np.empty(m, dtype=object)
    sh = sl = np.nan
    sh_live = sl_live = False          # a swing can be broken once
    trend = 0
    for t in range(m):
        i = t - n                      # the pivot confirmed by this bar's close
        if i >= 0:
            if ph[i]:
                sh, sh_live = high[i], True
            if pl[i]:
                sl, sl_live = low[i], True
        ev = ""
        if sh_live and close[t] > sh:
            ev = "BOS_UP" if trend == 1 else "CHOCH_UP"
            trend, sh_live = 1, False
        elif sl_live and close[t] < sl:
            ev = "BOS_DOWN" if trend == -1 else "CHOCH_DOWN"
            trend, sl_live = -1, False
        sh_out[t], sl_out[t], trend_out[t], event[t] = sh, sl, trend, ev
    return pd.DataFrame({"timestamp": df["timestamp"].to_numpy(), "sh": sh_out, "sl": sl_out,
                         "trend": trend_out, "event": event})


def session_of(ts: pd.Series) -> pd.Series:
    h = pd.to_datetime(ts, utc=True).dt.hour
    out = pd.Series("OFF", index=ts.index)
    for name, (a, b) in SESSIONS.items():
        out[(h >= a) & (h < b)] = name
    return out


def asia_range(df: pd.DataFrame) -> pd.DataFrame:
    """The current UTC day's Asia high/low, available only once the Asia session is over (07:00)."""
    ts = pd.to_datetime(df["timestamp"], utc=True)
    day, hour = ts.dt.normalize(), ts.dt.hour
    a, b = SESSIONS["ASIA"]
    asia = df[(hour >= a) & (hour < b)].groupby(day[(hour >= a) & (hour < b)])
    rng = pd.DataFrame({"asia_high": asia["high"].max(), "asia_low": asia["low"].min()})
    out = pd.DataFrame({"day": day}).join(rng, on="day")[["asia_high", "asia_low"]]
    out[hour < b] = np.nan
    return out.reset_index(drop=True)


def sweeps(df: pd.DataFrame, st: pd.DataFrame, asia: pd.DataFrame | None = None) -> pd.DataFrame:
    """Liquidity sweeps at each candle against the levels known BEFORE it (previous candle's swings and
    the Asia range): sweep_low = wick below a level, close back above it (sweep_high mirrored)."""
    low, high, close = (df[k].to_numpy(float) for k in ("low", "high", "close"))
    prev_sl = np.r_[np.nan, st["sl"].to_numpy(float)[:-1]]
    prev_sh = np.r_[np.nan, st["sh"].to_numpy(float)[:-1]]
    lv_low, lv_high = [prev_sl], [prev_sh]
    if asia is not None:
        lv_low.append(asia["asia_low"].to_numpy(float))
        lv_high.append(asia["asia_high"].to_numpy(float))
    sweep_low = np.zeros(len(df), bool)
    sweep_high = np.zeros(len(df), bool)
    with np.errstate(invalid="ignore"):
        for lv in lv_low:
            sweep_low |= (low < lv) & (close > lv)
        for lv in lv_high:
            sweep_high |= (high > lv) & (close < lv)
    return pd.DataFrame({"sweep_low": sweep_low, "sweep_high": sweep_high})
