from __future__ import annotations

import pandas as pd
import pytest

from app.ml.walk_forward import run_walk_forward


def test_walk_forward_produces_non_overlapping_sequential_windows(synthetic_ohlcv):
    results = run_walk_forward(
        synthetic_ohlcv,
        symbol="TESTUSD",
        timeframe="1h",
        model_type="logistic_regression",
        train_bars=400,
        test_bars=200,
    )
    assert len(results) >= 1
    for r in results:
        assert r.train_end <= r.test_start
        assert "total_return_pct" in r.__dict__

    # Windows should be in increasing chronological order.
    starts = [r.test_start for r in results]
    assert starts == sorted(starts)


def test_rolling_windows_keep_a_fixed_train_size(synthetic_ohlcv):
    results = run_walk_forward(
        synthetic_ohlcv, symbol="TESTUSD", timeframe="1h",
        model_type="logistic_regression", train_bars=400, test_bars=200,
    )
    assert len(results) >= 2
    assert {r.train_size for r in results} == {400}
    assert len({r.train_start for r in results}) == len(results)  # start moves forward


def test_expanding_windows_grow_from_a_fixed_start(synthetic_ohlcv):
    results = run_walk_forward(
        synthetic_ohlcv, symbol="TESTUSD", timeframe="1h",
        model_type="logistic_regression", train_bars=400, test_bars=200,
        window_mode="expanding",
    )
    assert len(results) >= 2
    assert len({r.train_start for r in results}) == 1
    sizes = [r.train_size for r in results]
    assert sizes[0] == 400
    assert sizes == sorted(sizes) and sizes[-1] > sizes[0]


def test_purge_gap_keeps_training_labels_out_of_the_test_period(synthetic_ohlcv):
    """Each training label looks `lookahead` bars ahead, so the gap between the
    last training bar and the first test bar must be at least that many bars."""
    from app.config import settings

    lookahead = settings.lookahead_period
    results = run_walk_forward(
        synthetic_ohlcv, symbol="TESTUSD", timeframe="1h",
        model_type="logistic_regression", train_bars=400, test_bars=200,
    )
    for r in results:
        gap_bars = (pd.Timestamp(r.test_start) - pd.Timestamp(r.train_end)) / pd.Timedelta("1h")
        assert gap_bars > lookahead


def test_rejects_unknown_window_mode(synthetic_ohlcv):
    with pytest.raises(ValueError):
        run_walk_forward(synthetic_ohlcv, symbol="TESTUSD", timeframe="1h", window_mode="sideways")
