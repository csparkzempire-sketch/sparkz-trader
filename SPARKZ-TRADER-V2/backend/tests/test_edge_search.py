"""Edge search: no strategy variant looks ahead, and costs are charged on every change of position."""

import numpy as np
import pandas as pd

from app.market.providers.mock_provider import generate_candles
from app.research import edge_search as es


def hourly(n=24 * 900, seed=5):
    return generate_candles("XAUUSD", "1h", n, "normal", seed=seed).reset_index(drop=True)


def test_no_variant_looks_ahead():
    h1 = hourly()
    full = es.variants(h1, es.daily(h1))
    for cut in (24 * 500 + 7, 24 * 700 + 15):
        part = h1.iloc[:cut].reset_index(drop=True)
        short = es.variants(part, es.daily(part))
        for name, (_, pos) in full.items():
            a, b = pos.iloc[:cut - 30].to_numpy(), short[name][1].iloc[:cut - 30].to_numpy()
            # the last day of a truncated series may be an unfinished daily candle; everything before must match
            assert np.array_equal(a, b), name


def test_costs_follow_turnover():
    h1 = hourly(24 * 60)
    flat = pd.Series(0.0, index=h1.index)
    assert es.evaluate(h1, flat, 0.54, 0.1, "2000-01-01", "2100-01-01")["net_usd_0.01lot"] == 0
    flip = pd.Series(np.where(np.arange(len(h1)) % 2, 1.0, -1.0), index=h1.index)
    r = es.evaluate(h1, flip, 0.54, 0.1, "2000-01-01", "2100-01-01")
    assert r["cost_usd_0.01lot"] > 0.37 * 2 * (len(h1) - 2)          # each flip trades 2 units
    hold = pd.Series(1.0, index=h1.index)
    rb = es.evaluate(h1, hold, 0.54, 0.1, "2000-01-01", "2100-01-01")
    assert abs(rb["beta_to_gold"] - 1) < 1e-6 and rb["cost_usd_0.01lot"] < 1
