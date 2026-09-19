"""Volatility indicators: ATR, Bollinger Bands, rolling volatility."""

from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = series.rolling(window=period, min_periods=period).mean()
    std = series.rolling(window=period, min_periods=period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_width": (upper - lower) / mid})


def rolling_volatility(series: pd.Series, period: int = 20) -> pd.Series:
    """Rolling std of log returns — a standard realized-volatility proxy."""
    log_ret = np.log(series / series.shift(1))
    return log_ret.rolling(window=period, min_periods=period).std()


def add_volatility_indicators(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    df = df.copy()
    df["atr"] = atr(df, atr_period)
    bb = bollinger_bands(df["close"])
    df["bb_mid"] = bb["bb_mid"]
    df["bb_upper"] = bb["bb_upper"]
    df["bb_lower"] = bb["bb_lower"]
    df["bb_width"] = bb["bb_width"]
    df["rolling_volatility"] = rolling_volatility(df["close"])
    return df
