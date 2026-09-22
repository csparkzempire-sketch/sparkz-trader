"""Volume indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd


def volume_change(series: pd.Series) -> pd.Series:
    """
    Percent change in volume. Real trading volume can legitimately be
    exactly 0 for a bar (an illiquid hour, a data gap) even when it's
    mostly nonzero -- found live on BTC-USD, where 0 -> nonzero the next
    bar makes plain pct_change() produce +inf, not NaN. dropna()-based
    leak guards downstream never touch inf (it isn't null), so it sails
    straight through to the model and sklearn correctly refuses it
    ("Input X contains infinity"). Converting any resulting +/-inf to NaN
    here makes it behave like any other missing/undefined feature value
    (dropped, not crashed on) without fabricating a numeric magnitude for
    what is, in that specific transition, an undefined percent change.
    """
    pct = series.pct_change()
    return pct.replace([np.inf, -np.inf], np.nan)


def rolling_volume_average(series: pd.Series, period: int = 20) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def add_volume_indicators(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    df = df.copy()
    df["volume_change"] = volume_change(df["volume"])
    df["volume_avg"] = rolling_volume_average(df["volume"], period)
    return df
