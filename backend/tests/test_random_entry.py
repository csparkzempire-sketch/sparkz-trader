"""Randomised signals for the paper accounts' random-entry check."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.research.random_entry import random_direction, random_timing


def test_random_timing_keeps_counts_and_warmup():
    sig = pd.Series(["HOLD"] * 3 + ["BUY", "HOLD", "SELL", "HOLD", "BUY", "HOLD", "HOLD"])
    eligible = np.array([False] * 3 + [True] * 7)
    out = random_timing(sig, eligible, np.random.default_rng(1))
    assert list(out[:3]) == ["HOLD"] * 3                      # warm-up bars untouched
    assert sorted(out) == sorted(sig)                          # same number of each signal
    # the order of signals is preserved up to rotation (runs stay intact)
    tail = list(sig[3:])
    assert any(list(out[3:]) == tail[k:] + tail[:k] for k in range(len(tail)))
    seen = {tuple(random_timing(sig, eligible, np.random.default_rng(k))) for k in range(20)}
    assert len(seen) > 1                                       # actually moves them


def test_random_direction_keeps_timing():
    sig = pd.Series(["HOLD", "BUY", "HOLD", "SELL", "BUY"] * 20)
    out = random_direction(sig, np.random.default_rng(2))
    fire = sig.isin(["BUY", "SELL"]).to_numpy()
    assert (out[~fire] == "HOLD").all() and np.isin(out[fire], ["BUY", "SELL"]).all()
    assert set(out[fire]) == {"BUY", "SELL"}
    runs = pd.Series(["BUY", "BUY", "HOLD", "SELL", "SELL", "SELL", "HOLD"] * 30)
    r = random_direction(runs, np.random.default_rng(3))
    for start in range(0, len(runs), 7):                       # a run always gets one direction
        assert r[start] == r[start + 1] and r[start + 3] == r[start + 4] == r[start + 5]
