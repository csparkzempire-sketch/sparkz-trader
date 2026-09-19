from __future__ import annotations

import pandas as pd

from app.data.validator import DataValidationError, validate_and_clean
import pytest


def test_clean_data_passes_through_mostly_unchanged(synthetic_ohlcv):
    clean, report = validate_and_clean(synthetic_ohlcv)
    assert report.duplicates_removed == 0
    assert report.invalid_ohlc_removed == 0
    assert clean["timestamp"].is_monotonic_increasing


def test_removes_duplicates_and_sorts_and_fixes_invalid(messy_ohlcv):
    clean, report = validate_and_clean(messy_ohlcv)
    assert report.duplicates_removed >= 1
    assert report.invalid_ohlc_removed >= 1
    assert report.missing_volume_filled >= 1
    assert clean["timestamp"].is_monotonic_increasing
    assert clean["timestamp"].duplicated().sum() == 0
    # No invalid OHLC should remain
    assert (clean["high"] >= clean["low"]).all()


def test_missing_required_columns_raises():
    df = pd.DataFrame({"timestamp": [1, 2], "open": [1, 2]})
    with pytest.raises(DataValidationError):
        validate_and_clean(df)


def test_empty_dataframe_raises():
    with pytest.raises(DataValidationError):
        validate_and_clean(pd.DataFrame())
