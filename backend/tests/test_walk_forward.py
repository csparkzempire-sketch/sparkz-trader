from __future__ import annotations

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
