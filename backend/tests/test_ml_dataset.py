from __future__ import annotations

import pandas as pd
import pytest

from app.config import Settings
from app.features.feature_engineering import FORBIDDEN_FEATURE_COLUMNS
from app.ml.dataset import LeakageError, add_labels, build_dataset


def _cfg(**overrides) -> Settings:
    base = Settings()
    return base.model_copy(update=overrides)


def test_add_labels_target_matches_manual_calc(synthetic_ohlcv):
    lookahead = 5
    threshold = 0.0
    labeled = add_labels(synthetic_ohlcv, lookahead_period=lookahead, target_return_threshold=threshold)

    # Manually verify a handful of rows.
    for i in [0, 10, 100, len(labeled) - lookahead - 1]:
        expected_future_close = synthetic_ohlcv["close"].iloc[i + lookahead]
        expected_return = expected_future_close / synthetic_ohlcv["close"].iloc[i] - 1.0
        assert labeled["future_return"].iloc[i] == pytest.approx(expected_return)
        expected_target = 1 if expected_return > threshold else 0
        assert labeled["target"].iloc[i] == expected_target


def test_add_labels_last_rows_are_unlabeled(synthetic_ohlcv):
    lookahead = 5
    labeled = add_labels(synthetic_ohlcv, lookahead_period=lookahead)
    tail = labeled.tail(lookahead)
    assert tail["future_return"].isna().all()
    assert tail["target"].isna().all()


def test_build_dataset_chronological_split_no_overlap(synthetic_ohlcv):
    dataset = build_dataset(synthetic_ohlcv)

    train_end_ts = dataset.train_period[1]
    val_start_ts = dataset.val_period[0]
    val_end_ts = dataset.val_period[1]
    test_start_ts = dataset.test_period[0]

    assert train_end_ts <= val_start_ts
    assert val_end_ts <= test_start_ts

    # Row counts roughly match configured fractions (within rounding).
    n = len(dataset.X_train) + len(dataset.X_val) + len(dataset.X_test)
    assert len(dataset.X_train) / n == pytest.approx(0.70, abs=0.05)
    assert len(dataset.X_val) / n == pytest.approx(0.15, abs=0.05)


def test_build_dataset_never_shuffles(synthetic_ohlcv):
    dataset = build_dataset(synthetic_ohlcv)
    full_timestamps = dataset.full_df["timestamp"]
    assert full_timestamps.is_monotonic_increasing


def test_feature_columns_exclude_forbidden_columns(synthetic_ohlcv):
    dataset = build_dataset(synthetic_ohlcv)
    assert not (set(dataset.feature_columns) & FORBIDDEN_FEATURE_COLUMNS)
    assert "future_return" not in dataset.feature_columns
    assert "future_close" not in dataset.feature_columns
    assert "target" not in dataset.feature_columns
    assert "close" not in dataset.feature_columns  # raw price itself is excluded too


def test_leakage_error_raised_if_forbidden_column_injected(monkeypatch, synthetic_ohlcv):
    """Simulate a bug where a future-looking column leaks into
    get_feature_columns' output, and confirm build_dataset fails loudly."""
    import app.ml.dataset as dataset_mod

    original_get_feature_columns = dataset_mod.get_feature_columns

    def _poisoned_get_feature_columns(df):
        cols = original_get_feature_columns(df)
        return cols + ["future_return"]

    monkeypatch.setattr(dataset_mod, "get_feature_columns", _poisoned_get_feature_columns)

    with pytest.raises(LeakageError):
        build_dataset(synthetic_ohlcv)


def test_build_dataset_raises_on_insufficient_data():
    tiny = pd.DataFrame(
        {
            "timestamp": pd.date_range("2023-01-01", periods=10, freq="1h", tz="UTC"),
            "open": [1.0] * 10,
            "high": [1.01] * 10,
            "low": [0.99] * 10,
            "close": [1.0] * 10,
            "volume": [100.0] * 10,
        }
    )
    with pytest.raises(ValueError):
        build_dataset(tiny)
