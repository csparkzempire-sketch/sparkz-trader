from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.momentum import macd, rsi
from app.indicators.trend import ema, sma
from app.indicators.volatility import atr, bollinger_bands


def test_ema_matches_pandas_ewm_definition():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0] * 10)
    result = ema(s, 5)
    expected = s.ewm(span=5, adjust=False, min_periods=5).mean()
    pd.testing.assert_series_equal(result, expected)


def test_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    result = sma(s, 3)
    assert np.isnan(result.iloc[0])
    assert np.isnan(result.iloc[1])
    assert result.iloc[2] == 2.0
    assert result.iloc[3] == 3.0
    assert result.iloc[4] == 4.0


def test_rsi_bounds(synthetic_ohlcv):
    r = rsi(synthetic_ohlcv["close"], 14)
    valid = r.dropna()
    assert (valid >= 0).all()
    assert (valid <= 100).all()


def test_rsi_all_gains_is_100():
    s = pd.Series(range(1, 30), dtype=float)  # strictly increasing
    r = rsi(s, 14)
    assert r.dropna().iloc[-1] == 100.0


def test_macd_columns(synthetic_ohlcv):
    m = macd(synthetic_ohlcv["close"])
    assert set(m.columns) == {"macd", "macd_signal", "macd_hist"}
    # hist = macd - signal
    valid = m.dropna()
    np.testing.assert_allclose(valid["macd_hist"], valid["macd"] - valid["macd_signal"], atol=1e-9)


def test_atr_positive(synthetic_ohlcv):
    a = atr(synthetic_ohlcv, 14)
    assert (a.dropna() >= 0).all()


def test_bollinger_bands_ordering(synthetic_ohlcv):
    bb = bollinger_bands(synthetic_ohlcv["close"])
    valid = bb.dropna()
    assert (valid["bb_upper"] >= valid["bb_mid"]).all()
    assert (valid["bb_mid"] >= valid["bb_lower"]).all()


def test_indicators_do_not_use_future_data():
    """If we truncate the series at bar i, indicator value at bar i must be
    identical whether or not future bars exist — this directly tests for
    look-ahead bias."""
    s = pd.Series(np.linspace(1.0, 1.5, 200))
    full_ema = ema(s, 20)
    truncated_ema = ema(s.iloc[:100], 20)
    pd.testing.assert_series_equal(full_ema.iloc[:100], truncated_ema, check_names=False)

    full_rsi = rsi(s, 14)
    truncated_rsi = rsi(s.iloc[:100], 14)
    pd.testing.assert_series_equal(full_rsi.iloc[:100], truncated_rsi, check_names=False)
