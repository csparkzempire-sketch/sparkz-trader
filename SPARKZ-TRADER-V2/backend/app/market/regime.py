"""
Market regime per bar, from that bar's trailing features only.

Two underlying labels:
- trend_regime: TRENDING_UP, TRENDING_DOWN, RANGING, UNCERTAIN
    TRENDING_UP    ADX >= trend_adx_min, EMA20 > EMA50, close > EMA50, +DI > -DI
    TRENDING_DOWN  the mirror image
    RANGING        ADX <= range_adx_max
    UNCERTAIN      anything else
- vol_regime: HIGH_VOLATILITY, NORMAL_VOLATILITY, LOW_VOLATILITY, from where ATR%
  sits in its own trailing window

and one headline `regime` (what the dashboard shows), in priority order:
  WARMUP           indicators not ready yet
  HIGH_VOLATILITY  volatility in the top band, whatever the trend
  TRENDING_UP / TRENDING_DOWN / RANGING
  LOW_VOLATILITY   no clear trend and unusually quiet
  UNCERTAIN        everything else
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import AnalysisCfg

REGIMES = ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY", "UNCERTAIN", "WARMUP"]


def label_regimes(f: pd.DataFrame, cfg: AnalysisCfg | None = None) -> pd.DataFrame:
    cfg = cfg or AnalysisCfg()
    up = (f["adx"] >= cfg.trend_adx_min) & (f["ema_fast"] > f["ema_slow"]) & (f["close"] > f["ema_slow"]) \
        & (f["plus_di"] > f["minus_di"])
    down = (f["adx"] >= cfg.trend_adx_min) & (f["ema_fast"] < f["ema_slow"]) & (f["close"] < f["ema_slow"]) \
        & (f["minus_di"] > f["plus_di"])
    ranging = f["adx"] <= cfg.range_adx_max
    trend = np.select([up, down, ranging], ["TRENDING_UP", "TRENDING_DOWN", "RANGING"], "UNCERTAIN")
    high = f["vol_percentile"] >= cfg.high_vol_percentile
    low = f["vol_percentile"] <= cfg.low_vol_percentile
    vol = np.select([high, low], ["HIGH_VOLATILITY", "LOW_VOLATILITY"], "NORMAL_VOLATILITY")
    head = np.select([high, trend != "UNCERTAIN", low], ["HIGH_VOLATILITY", trend, "LOW_VOLATILITY"], "UNCERTAIN")
    warm = f[["adx", "ema_slow", "ema_trend", "vol_percentile", "rsi", "atr"]].isna().any(axis=1)
    out = f.copy()
    out["trend_regime"] = np.where(warm, "WARMUP", trend)
    out["vol_regime"] = np.where(warm, "WARMUP", vol)
    out["regime"] = np.where(warm, "WARMUP", head)
    return out
