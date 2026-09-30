"""The research hook for alternative entry rules, and the random-entry benchmarks."""

from __future__ import annotations

from app.backtest.runner import prepare_features, run_backtest
from app.research.entry_validation import control, random_direction, random_long
from app.strategy.entry_engine import EntryDecision, evaluate_entry
from tests.conftest import make_candles


def test_default_entry_rules_are_unchanged_by_the_hook():
    s = control()
    f = prepare_features(make_candles(2500, drift=0.02, vol=1.5, seed=61), s)
    a = run_backtest(s, features=f)
    b = run_backtest(s, features=f, entry_fn=evaluate_entry)
    assert [x.to_dict() for x in a.baskets] == [x.to_dict() for x in b.baskets]


def test_random_direction_fires_at_the_same_moments():
    s = control()
    f = prepare_features(make_candles(2500, drift=0.02, vol=1.5, seed=62), s)
    real = run_backtest(s, features=f)
    rnd = run_backtest(s, features=f, entry_fn=random_direction(1))
    assert real.baskets[0].opened_at == rnd.baskets[0].opened_at     # the first entry can't differ in timing
    dirs = {b.direction for k in range(10) for b in run_backtest(s, features=f, entry_fn=random_direction(k)).baskets}
    assert dirs == {"BUY", "SELL"}


def test_random_long_only_buys_and_is_reproducible():
    s = control()
    f = prepare_features(make_candles(2500, seed=63), s)
    a = run_backtest(s, features=f, entry_fn=random_long(5, 0.05))
    b = run_backtest(s, features=f, entry_fn=random_long(5, 0.05))
    assert a.baskets and {x.direction for x in a.baskets} == {"BUY"}
    assert [x.pnl for x in a.baskets] == [x.pnl for x in b.baskets]


def test_an_entry_fn_can_never_bypass_risk_limits():
    s = control()
    f = prepare_features(make_candles(2500, seed=64), s)
    always = lambda row, cfg: EntryDecision("BUY") if row["regime"] != "WARMUP" else EntryDecision("NONE")
    r = run_backtest(s, features=f, entry_fn=always)
    assert all(b.positions == 1 for b in r.baskets)   # the control's max_positions still holds
