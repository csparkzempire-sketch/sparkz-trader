"""Tests for app.features.feature_engineering.

The zero-volume tests here exist because of a real bug found against live
EUR/USD data from Yahoo Finance: yfinance reports volume as a constant 0
for FX pairs. volume_change() is a pct_change(), and 0/0 is NaN for every
single row when volume never leaves zero -- that NaN column then poisoned
build_dataset()'s leak-guarded dropna step, silently emptying the entire
training set. The synthetic_ohlcv fixture used everywhere else in this
suite has random nonzero volume, so this class of bug was invisible to the
existing tests. These tests exercise the zero-volume path directly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.features.feature_engineering import build_feature_matrix, get_feature_columns
from app.ml.dataset import build_dataset


@pytest.fixture
def zero_volume_ohlcv(synthetic_ohlcv: pd.DataFrame) -> pd.DataFrame:
    """The same synthetic price data as everywhere else, but with volume
    forced to a constant 0 -- exactly what yfinance returns for FX pairs."""
    df = synthetic_ohlcv.copy()
    df["volume"] = 0.0
    return df


def test_normal_volume_produces_volume_features(synthetic_ohlcv: pd.DataFrame):
    featured = build_feature_matrix(synthetic_ohlcv)
    assert "volume_change" in featured.columns
    assert "volume_avg" in featured.columns


def test_zero_volume_skips_volume_features_instead_of_producing_all_nan(zero_volume_ohlcv: pd.DataFrame):
    featured = build_feature_matrix(zero_volume_ohlcv)
    # The features must simply be absent, not present-but-entirely-NaN --
    # an all-NaN column is what caused the original bug.
    assert "volume_change" not in featured.columns
    assert "volume_avg" not in featured.columns


def test_zero_volume_dataset_is_still_buildable_and_nonempty(zero_volume_ohlcv: pd.DataFrame):
    """This is the actual regression test: build_dataset must not raise
    'No usable rows remain' just because a symbol has no real volume data."""
    dataset = build_dataset(zero_volume_ohlcv)
    assert len(dataset.X_train) > 0
    assert len(dataset.X_val) > 0
    assert len(dataset.X_test) > 0
    assert "volume_change" not in dataset.feature_columns
    assert "volume_avg" not in dataset.feature_columns


def test_zero_volume_yields_same_row_count_as_nonzero_volume(synthetic_ohlcv: pd.DataFrame, zero_volume_ohlcv: pd.DataFrame):
    """Dropping volume features shouldn't cost usable rows elsewhere --
    the two runs should warm up and drop rows identically apart from the
    volume columns themselves."""
    with_volume = build_feature_matrix(synthetic_ohlcv)
    without_volume = build_feature_matrix(zero_volume_ohlcv)
    assert len(with_volume) == len(without_volume)


def test_nan_volume_column_also_treated_as_no_volume_data(synthetic_ohlcv: pd.DataFrame):
    """Entirely-missing volume (all NaN, e.g. a data source that omits it
    altogether) should be handled the same way as all-zero."""
    df = synthetic_ohlcv.copy()
    df["volume"] = np.nan
    featured = build_feature_matrix(df)
    assert "volume_change" not in featured.columns
    assert "volume_avg" not in featured.columns
