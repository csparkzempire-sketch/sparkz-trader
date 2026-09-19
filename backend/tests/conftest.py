from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def synthetic_ohlcv() -> pd.DataFrame:
    """
    Deterministic synthetic OHLCV data for tests. Uses a fixed random seed
    so tests are reproducible. A mild upward random walk with realistic
    intrabar high/low/open placement.
    """
    rng = np.random.default_rng(42)
    n = 1500
    timestamps = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")

    returns = rng.normal(loc=0.00005, scale=0.0015, size=n)
    close = 1.1000 * np.exp(np.cumsum(returns))

    open_ = np.empty(n)
    open_[0] = close[0] * (1 - 0.0002)
    open_[1:] = close[:-1]

    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0007, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0007, n)))
    volume = rng.integers(100, 1000, n).astype(float)

    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    return df


@pytest.fixture
def messy_ohlcv(synthetic_ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Same data but with duplicates, an invalid candle, missing volume, and shuffled order."""
    df = synthetic_ohlcv.copy()

    # Duplicate a row.
    df = pd.concat([df, df.iloc[[10]]], ignore_index=True)

    # Corrupt one candle (high < low).
    bad_idx = 20
    df.loc[bad_idx, "high"] = df.loc[bad_idx, "low"] - 0.001

    # Missing volume.
    df.loc[30, "volume"] = np.nan

    # Shuffle order (validator must re-sort).
    df = df.sample(frac=1.0, random_state=1).reset_index(drop=True)
    return df
