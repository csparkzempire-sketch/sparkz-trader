"""
Trend indicators.

All functions are pure: they take a Series/DataFrame and return a new
Series, computed only from current and past values (pandas' `.ewm()` and
`.rolling()` are causal by construction — they never look forward — so as
long as we don't pass `center=True` anywhere, there is no look-ahead bias).
"""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average. `adjust=False` gives the standard
    recursive EMA definition used in trading platforms."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=period, min_periods=period).mean()


def add_trend_indicators(df: pd.DataFrame, ema_fast: int = 20, ema_slow: int = 50, ema_long: int = 200) -> pd.DataFrame:
    df = df.copy()
    df[f"ema_{ema_fast}"] = ema(df["close"], ema_fast)
    df[f"ema_{ema_slow}"] = ema(df["close"], ema_slow)
    df[f"ema_{ema_long}"] = ema(df["close"], ema_long)
    df["sma_20"] = sma(df["close"], 20)
    df["sma_50"] = sma(df["close"], 50)
    return df
