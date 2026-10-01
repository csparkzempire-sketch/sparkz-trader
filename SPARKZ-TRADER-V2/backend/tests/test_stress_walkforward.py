"""Stress tests, critical failure scenario, walk-forward and parameter-lab guards."""

import pytest

from app.backtest.engine import run_backtest
from app.backtest.stress_test import critical_failure, execution_stress
from app.backtest.walk_forward import MAX_CANDIDATES, expand, parameter_lab, walk_forward
from app.config import load_settings
from app.market.providers.mock_provider import generate_candles


def test_critical_failure_shows_the_loss():
    out = critical_failure(load_settings("atr_grid", env={}))
    v = out["variants"]
    assert out["synthetic"] is True
    assert v["as_configured"]["outcome"] == "LOSS_LIMIT"
    assert v["as_configured"]["realized_pnl"] < 0
    # without the basket loss limit the same move costs more (or trips the account halt)
    assert v["no_close_at_loss_limit"]["realized_pnl"] < v["as_configured"]["realized_pnl"]
    # multiplier sizing carries far more exposure than fixed sizing
    assert v["multiplier_sizing_HIGH_RISK"]["max_exposure_usd"] > 3 * v["as_configured"]["max_exposure_usd"]
    p = v["as_configured"]["path"]
    assert {"floating_pnl", "exposure_usd", "margin_usd", "positions", "to_break_even", "mae"} <= set(p[1])


def test_execution_stress_costs_more():
    c = generate_candles("XAUUSD", "15m", 1500, "normal", seed=3)
    out = execution_stress(load_settings(env={}), c)["variants"]
    assert out["spread_x3_slippage_x5"]["costs_usd"] > out["base"]["costs_usd"]


def test_parameter_lab_caps_the_search():
    with pytest.raises(ValueError):
        expand({"grid.atr_multiplier": list(range(1, MAX_CANDIDATES + 2))})
    assert expand({"grid.atr_multiplier": [0.5, 1.0], "risk.max_positions": [3, 5]})[3] == \
        {"grid": {"atr_multiplier": 1.0}, "risk": {"max_positions": 5}}


def test_walk_forward_is_chronological_and_reports_every_candidate():
    c = generate_candles("XAUUSD", "15m", 2600, "normal", seed=4)
    wf = walk_forward(load_settings(env={}), c, {"grid.atr_multiplier": [0.5, 1.0]}, folds=2)
    for f in wf["folds"]:
        assert f["train_period"][1] < f["validation_period"][0] <= f["validation_period"][1] < f["test_period"][0]
        assert len(f["candidates"]) == 2
    assert wf["summary"]["candidates_tried"] == 2


def test_parameter_lab_reports_split_and_test():
    c = generate_candles("XAUUSD", "15m", 2600, "normal", seed=5)
    out = parameter_lab(load_settings(env={}), c, {"target.fixed_usd": [5, 10]})
    assert len(out["candidates"]) == 2 and "test" in out and out["split"]["test"][0] > out["split"]["validation"][1]


def test_window_never_trades_outside_its_dates():
    c = generate_candles("XAUUSD", "15m", 2000, "reversals", seed=6)
    r = run_backtest(load_settings(env={}), c, start=800, end=1400)
    lo, hi = c["timestamp"].iloc[800], c["timestamp"].iloc[1399] + (c["timestamp"].iloc[1] - c["timestamp"].iloc[0])
    assert all(lo <= b.opened_at and b.closed_at <= hi for b in r.robot.completed)


def test_finer_candles_drive_the_intrabar_path():
    c5 = generate_candles("XAUUSD", "5m", 1800, "normal", seed=7)
    c15 = c5.set_index("timestamp").resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
    r = run_backtest(load_settings(env={}), c15, path_candles=c5)
    assert r.robot.path_bars == len(c15)
    with pytest.raises(ValueError):
        run_backtest(load_settings(env={}), c15, path_candles=c15.iloc[::2])
