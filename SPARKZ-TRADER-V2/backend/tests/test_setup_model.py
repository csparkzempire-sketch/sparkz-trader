"""Setup-filter model: features use no future candles, walk-forward never trains on a test month's outcomes."""

import numpy as np
import pandas as pd
import pytest

from app.research import setup_model as sm
from app.research.scalp import ScalpParams, signals


def walk(days=4, seed=2):
    rng = np.random.default_rng(seed)
    n = days * 24 * 60
    c = 2600 + rng.normal(0, 0.4, n).cumsum()
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"timestamp": pd.date_range("2025-01-06", periods=n, freq="1min", tz="UTC"), "open": o,
                         "high": np.maximum(o, c) + 0.2, "low": np.minimum(o, c) - 0.2, "close": c, "volume": 1.0})


@pytest.mark.parametrize("tf", ["1m", "5m"])
def test_features_do_not_change_when_later_candles_are_removed(tf):
    m1 = walk(days=12)
    p = ScalpParams(tf=tf, swing_n=2, variant="choch")
    cut = pd.Timestamp("2025-01-15 12:00", tz="UTC")
    sig = signals(m1, p)
    early = sig[pd.to_datetime(sig["time"], utc=True) <= cut]
    assert len(early) > 5
    full = sm.features(m1, p, early)
    short = sm.features(m1[m1["timestamp"] < cut].reset_index(drop=True), p, early)
    pd.testing.assert_frame_equal(full, short)


def test_walk_forward_trains_only_on_outcomes_known_before_the_month():
    rng = np.random.default_rng(0)
    et = pd.date_range("2024-10-01", "2025-03-31", freq="30min", tz="UTC")
    rows = pd.DataFrame(rng.normal(size=(len(et), len(sm.FEATURES))), columns=sm.FEATURES)
    rows["entry_time"], rows["exit_time"] = et, et + pd.Timedelta("90min")
    rows["r"] = rng.normal(size=len(et))
    seen = []

    class Spy:
        def fit(self, X, y):
            seen.append(len(y))

        def predict(self, X):
            return np.zeros(len(X))

    pred = sm.walk_forward(rows, "2025-01-01", min_train=10, model_factory=Spy)
    assert pred[rows["entry_time"] < pd.Timestamp("2025-01-01", tz="UTC")].isna().all()
    assert pred[rows["entry_time"] >= pd.Timestamp("2025-01-01", tz="UTC")].notna().all()
    for m0, n in zip(pd.date_range("2025-01-01", periods=3, freq="MS", tz="UTC"), seen):
        assert n == int((rows["exit_time"] < m0).sum())         # purged: exits before the month only
