"""Volume indicators."""

from __future__ import annotations

import pandas as pd


def volume_change(series: pd.Series) -> pd.Series:
    return series.pct_change()


def rolling_volume_average(series: pd.Series, period: int = 20) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def add_volume_indicators(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    df = df.copy()
    df["volume_change"] = volume_change(df["volume"])
    df["volume_avg"] = rolling_volume_average(df["volume"], period)
    return df
