"""Shared fixtures: a throwaway SQLite database per test and synthetic candles."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    import app.models.database as database

    path = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    return path


def make_candles(n: int = 600, start: float = 2000.0, drift: float = 0.0, vol: float = 1.0, seed: int = 1,
                 freq: str = "15min", t0: str = "2026-01-05 00:00") -> pd.DataFrame:
    """Random-walk OHLC with a per-bar drift (price units) and noise `vol`."""
    rng = np.random.default_rng(seed)
    steps = drift + rng.normal(0, vol, n)
    close = start + np.cumsum(steps)
    open_ = np.concatenate([[start], close[:-1]])
    wick = np.abs(rng.normal(0, vol * 0.5, n))
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    ts = pd.date_range(t0, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": high, "low": low, "close": close,
                         "volume": 100.0})


@pytest.fixture
def candles():
    return make_candles()
