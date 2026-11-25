"""Lot-escalation simulator: fills at bid/ask, flips, pyramid adds, stop-out, no credit for a target after a stop."""

import numpy as np
import pandas as pd

from app.research import lot_escalation as le


def path(prices, start="2025-03-03 10:00"):
    """1m candles moving linearly between the given mid prices (one candle per $0.50)."""
    mids = []
    for a, b in zip(prices[:-1], prices[1:]):
        n = max(1, int(abs(b - a) / 0.5))
        mids += list(np.linspace(a, b, n + 1)[:-1])
    mids.append(prices[-1])
    m = np.array(mids)
    t = list(pd.date_range(start, periods=len(m), freq="1min", tz="UTC"))
    o = np.r_[m[0], m[:-1]]
    return t, o, np.maximum(o, m), np.minimum(o, m), m


def sim(v, prices, balance=1000.0, trend=1.0, spread=0.24, slip=0.0, leverage=500.0):
    t, o, h, lo, c = path(prices)
    tr = np.zeros(len(c))
    tr[0] = trend
    return le.run(v, t, o, h, lo, c, tr, balance, spread, slip, leverage)


def test_flip_wins_after_one_flip():
    v = le.Flip("f", lot_per_100=0.001, mult=2.0, flip=3.0, target_usd=1.0, max_flips=8)
    r = sim(v, [3000, 2996, 2980])            # long loses 3, flips short with 0.02, falls: cycle target
    assert r["cycles"] == 1 and r["max_lots_open"] == 0.02
    assert abs(r["final_balance"] - 1001.0) < 1e-6        # exactly the 1 USD target


def test_flip_max_flips_takes_the_loss():
    v = le.Flip("f", lot_per_100=0.001, mult=2.0, flip=3.0, target_usd=1.0, max_flips=2)
    r = sim(v, [3000, 2996.5, 3001, 2996, 3001, 2996])   # chop: every leg stopped
    assert r["cycles"] >= 1 and r["worst_cycle_usd"] < 0 and r["final_balance"] < 1000


def test_pyramid_adds_and_target():
    v = le.Pyramid("p", lot_per_100=0.001, adds=[(5.0, 2.0)], target=10.0)
    r = sim(v, [3000, 3012])
    # 0.01 from 3000.12 and 0.02 from 3005.12 to the 3010 target (bid 3009.88): 9.76 + 2 x 4.76 = 19.28
    assert r["max_lots_open"] == 0.03 and abs(r["final_balance"] - 1019.28) < 1e-6


def test_pyramid_stop_out_blows_the_account():
    v = le.Pyramid("p", lot_per_100=0.2, adds=[], target=50.0)      # 0.01 lot on 5 USD, 1:2000
    r = sim(v, [3000, 2990], balance=5.0, leverage=2000.0)          # -10 x 1 oz = -10 > 5 USD
    assert r["blown"] and r["final_balance"] == 0 and r["cycles"] == 1


def test_trailing_stop_moves_to_previous_add():
    v = le.Pyramid("p", lot_per_100=0.001, adds=[(5.0, 1.0)], target=40.0, stop=5.0, trail=True)
    r = sim(v, [3000, 3006, 2990])
    # stop moved to 3000 after the add at 3005: closed at 3000 (bid 2999.88): -0.12 - 5.12 ... per 0.01 lot
    assert r["cycles"] >= 1 and abs(r["worst_cycle_usd"] - (-0.24 - 5.24)) < 1e-6


def test_flips_never_lose_more_than_the_account():
    v = le.Flip("f", lot_per_100=0.2, mult=2.0, flip=3.0, target_usd=1.0, max_flips=8)
    r = sim(v, [3000, 2996, 3003, 2995, 3005, 2993], balance=20.0, leverage=2000.0)
    assert r["final_balance"] >= 0 and r["worst_cycle_usd"] >= -20.0 and r["final_balance"] < 20
