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
from app.utils.time import timeframe_to_pandas_freq

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


def add_multi_timeframe_features(df: pd.DataFrame, base_timeframe: str, higher_timeframes: list[str], cfg=None) -> pd.DataFrame:
    """
    Adds higher-timeframe trend/momentum/volatility context (e.g. 4h, 1d
    EMA/RSI/ATR) onto a lower-timeframe (e.g. 1h) feature matrix.

    This is the one place in the pipeline where look-ahead bias is easiest
    to introduce by accident: a naive resample-and-forward-fill join would
    let a base-timeframe bar see a higher-timeframe candle's indicators
    before that candle has actually closed (e.g. a 1h bar at 10:00 seeing
    the 4h candle covering 08:00-12:00, when that candle doesn't close
    until 12:00). To prevent that, a higher-timeframe bar's indicators are
    only made available starting at that bar's CLOSE time (bar_open +
    bar_duration), joined via merge_asof(direction="backward") so each
    base row only ever sees the most recently CLOSED higher-timeframe bar.
    """
    cfg = cfg or settings
    base_delta = pd.Timedelta(timeframe_to_pandas_freq(base_timeframe))

    out = df.sort_values("timestamp").reset_index(drop=True)

    for htf in higher_timeframes:
        htf_freq = timeframe_to_pandas_freq(htf)
        htf_delta = pd.Timedelta(htf_freq)
        if htf_delta <= base_delta:
            logger.warning(
                "multi_timeframe_skip %s",
                kv(reason=f"higher_timeframe '{htf}' is not strictly longer than base_timeframe '{base_timeframe}'; skipped"),
            )
            continue

        ohlc = out.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
        resampled = (
            ohlc.resample(htf_freq, label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(subset=["open", "high", "low", "close"])
            .reset_index()  # "timestamp" here is each higher-timeframe bar's OPEN time
        )

        htf_feat = add_trend_indicators(resampled, cfg.ema_fast, cfg.ema_slow, cfg.ema_long)
        htf_feat = add_momentum_indicators(htf_feat, cfg.rsi_period)
        htf_feat = add_volatility_indicators(htf_feat, cfg.atr_period)
        htf_feat = add_ema_distance_features(htf_feat, cfg.ema_fast, cfg.ema_slow, cfg.ema_long)

        # A bar's indicator values are computed from its own close, so they
        # cannot be known/used until the bar has actually closed.
        htf_feat["available_at"] = htf_feat["timestamp"] + htf_delta

        feature_cols = get_feature_columns(htf_feat)
        suffix = f"_{htf}"
        htf_feat = (
            htf_feat[["available_at"] + feature_cols]
            .rename(columns={c: f"{c}{suffix}" for c in feature_cols})
            .sort_values("available_at")
        )

        out = pd.merge_asof(
            out.sort_values("timestamp"),
            htf_feat,
            left_on="timestamp",
            right_on="available_at",
            direction="backward",
        ).drop(columns=["available_at"])

    return out


def build_feature_matrix(
    raw_df: pd.DataFrame,
    cfg=None,
    timeframe: str | None = None,
    higher_timeframes: list[str] | None = None,
) -> pd.DataFrame:
    """
    Full feature pipeline: raw OHLCV -> indicators -> price structure ->
    EMA-distance features -> (optional) higher-timeframe context. Input
    must already be validated/cleaned and sorted chronologically (see
    app.data.validator).

    `higher_timeframes` (e.g. ["4h", "1d"]) adds leakage-safe multi-
    timeframe context columns -- see add_multi_timeframe_features. It
    requires `timeframe` (the base timeframe of raw_df) to be given too.
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

    if higher_timeframes:
        if not timeframe:
            raise ValueError("build_feature_matrix: `timeframe` is required when `higher_timeframes` is given.")
        df = add_multi_timeframe_features(df, timeframe, higher_timeframes, cfg)

    return df


def get_feature_columns(df: pd.DataFrame) -> list[str]:
    """
    Returns the list of columns safe to use as ML features: numeric columns
    minus raw OHLCV/timestamp/identifiers and anything in FORBIDDEN_FEATURE_COLUMNS.
    """
    exclude = {"timestamp", "open", "high", "low", "close", "volume", "regime", "signal"} | FORBIDDEN_FEATURE_COLUMNS
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    return [c for c in numeric_cols if c not in exclude]
