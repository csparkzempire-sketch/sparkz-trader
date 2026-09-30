"""
Market regime labels, per bar, from that bar's trailing features only.

Two independent labels:
- trend regime: TRENDING_UP, TRENDING_DOWN, RANGING, UNCERTAIN
    TRENDING_UP    ADX >= trend_adx_min, EMA20 > EMA50, close > EMA50, +DI > -DI
    TRENDING_DOWN  the mirror image
    RANGING        ADX <= range_adx_max
    UNCERTAIN      anything else: ADX in between, or trend signals that disagree
- volatility regime: HIGH_VOLATILITY, LOW_VOLATILITY, NORMAL_VOLATILITY
    from where ATR% sits in its own trailing window (vol_percentile)

Bars still in the indicator warm-up are WARMUP. The strategy never trades
WARMUP or UNCERTAIN bars.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import AnalysisCfg

TREND_REGIMES = ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "UNCERTAIN"]
VOL_REGIMES = ["HIGH_VOLATILITY", "NORMAL_VOLATILITY", "LOW_VOLATILITY"]


def label_regimes(f: pd.DataFrame, cfg: AnalysisCfg | None = None) -> pd.DataFrame:
    cfg = cfg or AnalysisCfg()
    up = (f["adx"] >= cfg.trend_adx_min) & (f["ema_fast"] > f["ema_slow"]) & (f["close"] > f["ema_slow"]) \
        & (f["plus_di"] > f["minus_di"])
    down = (f["adx"] >= cfg.trend_adx_min) & (f["ema_fast"] < f["ema_slow"]) & (f["close"] < f["ema_slow"]) \
        & (f["minus_di"] > f["plus_di"])
    ranging = f["adx"] <= cfg.range_adx_max
    regime = np.select([up, down, ranging], ["TRENDING_UP", "TRENDING_DOWN", "RANGING"], "UNCERTAIN")
    vol = np.select([f["vol_percentile"] >= cfg.high_vol_percentile, f["vol_percentile"] <= cfg.low_vol_percentile],
                    ["HIGH_VOLATILITY", "LOW_VOLATILITY"], "NORMAL_VOLATILITY")
    warm = f[["adx", "ema_slow", "ema_trend", "vol_percentile"]].isna().any(axis=1)
    out = f.copy()
    out["regime"] = np.where(warm, "WARMUP", regime)
    out["vol_regime"] = np.where(warm, "WARMUP", vol)
    return out
