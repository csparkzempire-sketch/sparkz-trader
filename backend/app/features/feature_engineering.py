"""
Feature engineering.

Combines all indicator modules and adds price-structure features. Every
feature here is computed using only information available at or before the
current candle's close — this is the file to check first if you suspect
look-ahead bias anywhere downstream.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import settings
from app.indicators.momentum import add_momentum_indicators
from app.indicators.trend import add_trend_indicators
from app.indicators.volatility import add_volatility_indicators
from app.indicators.volume import add_volume_indicators
from app.utils.logging import get_logger, kv

logger = get_logger(__name__)

# Columns that must NEVER be used as ML features — they either are the raw
# label ingredients or are only knowable in the future relative to a bar.
FORBIDDEN_FEATURE_COLUMNS = {
    "future_close",
    "future_return",
    "target",
    "regime_forward",
}


def add_price_structure_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["pct_return"] = df["close"].pct_change()
    df["log_return"] = np.log(df["close"] / df["close"].shift(1))
    df["candle_body"] = (df["close"] - df["open"]).abs()
    df["upper_wick"] = df["high"] - df[["open", "close"]].max(axis=1)
    df["lower_wick"] = df[["open", "close"]].min(axis=1) - df["low"]
    df["high_low_range"] = df["high"] - df["low"]
    return df


def add_ema_distance_features(df: pd.DataFrame, ema_fast: int, ema_slow: int, ema_long: int) -> pd.DataFrame:
    df = df.copy()
    for period in (ema_fast, ema_slow, ema_long):
        col = f"ema_{period}"
        if col in df.columns:
            df[f"dist_from_ema_{period}"] = (df["close"] - df[col]) / df[col]
    return df


def build_feature_matrix(raw_df: pd.DataFrame, cfg=None) -> pd.DataFrame:
    """
    Full feature pipeline: raw OHLCV -> indicators -> price structure ->
    EMA-distance features. Input must already be validated/cleaned and
    sorted chronologically (see app.data.validator).
    """
    cfg = cfg or settings
    df = raw_df.copy()

    df = add_trend_indicators(df, cfg.ema_fast, cfg.ema_slow, cfg.ema_long)
    df = add_momentum_indicators(df, cfg.rsi_period)
    df = add_volatility_indicators(df, cfg.atr_period)

    # Yahoo Finance reports volume as a constant 0 for FX pairs (no real
    # trade-volume data exists for spot forex there). Feeding that through
    # volume_change (a pct_change) produces NaN for EVERY row via 0/0,
    # which would otherwise poison the entire feature matrix once
    # build_dataset's leak-guarded dropna runs. Rather than fabricate a
    # fake "no change" signal for data that carries no real information,
    # we skip volume-derived features entirely when volume is effectively
    # constant/zero, and say so loudly.
    if (df["volume"].fillna(0) == 0).all():
        logger.warning(
            "volume_features_skipped %s",
            kv(reason="volume column is entirely zero/NaN (typical for FX pairs from Yahoo Finance); "
                      "volume_change/volume_avg would be uninformative or all-NaN, so they are omitted"),
        )
    else:
        df = add_volume_indicators(df)

    df = add_price_structure_features(df)
    df = add_ema_distance_features(df, cfg.ema_fast, cfg.ema_slow, cfg.ema_long)

    return df


def get_feature_columns(df: pd.DataFrame) -> list[str]:
    """
    Returns the list of columns safe to use as ML features: numeric columns
    minus raw OHLCV/timestamp/identifiers and anything in FORBIDDEN_FEATURE_COLUMNS.
    """
    exclude = {"timestamp", "open", "high", "low", "close", "volume", "regime", "signal"} | FORBIDDEN_FEATURE_COLUMNS
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    return [c for c in numeric_cols if c not in exclude]
