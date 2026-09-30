"""Phase 9: stress scenarios, Monte Carlo, sensitivity, walk-forward and the comparison lab."""

from __future__ import annotations

import numpy as np
import pytest

from app.backtest.comparison import compare
from app.backtest.monte_carlo import perturbation_runs, sensitivity, sequence_tests
from app.backtest.runner import prepare_features, run_backtest
from app.backtest.stress_test import inject_spike, overlay_ramp, run_stress, synthetic
from app.backtest.walk_forward import period_stability, walk_forward
from app.config import list_presets, load_config, load_preset
from tests.conftest import make_candles


@pytest.fixture(scope="module")
def data():
    return make_candles(2500, drift=0.01, vol=1.2, seed=21)


def test_synthetic_series_have_the_requested_drift():
    up = synthetic(2000, 2000.0, 0.001, 0.3, seed=1)
    down = synthetic(2000, 2000.0, 0.001, -0.3, seed=1)
    flat = synthetic(2000, 2000.0, 0.001, 0.0, mean_revert=0.05, seed=1)
    assert up["close"].iloc[-1] > 2000 * 1.5 and down["close"].iloc[-1] < 2000 / 1.5
    assert abs(flat["close"].iloc[-1] / 2000 - 1) < 0.02
    assert (up["high"] >= up[["open", "close"]].max(axis=1)).all()


def test_overlays_keep_bars_valid():
    df = make_candles(300)
    ramp = overlay_ramp(df, 100, 50, -30.0)
    assert ramp["close"].iloc[299] == pytest.approx(df["close"].iloc[299] - 30.0)
    spike = inject_spike(df, 100, 20, 5.0, -10.0)
    assert (spike["high"] >= spike[["open", "close"]].max(axis=1)).all()
    assert (spike["low"] <= spike[["open", "close"]].min(axis=1)).all()
    assert (spike["high"] - spike["low"]).iloc[105] > (df["high"] - df["low"]).iloc[105] * 4


def test_sequence_tests():
    pnls = [10.0] * 30 + [-50.0] * 4
    r = sequence_tests(pnls, 10_000, 10, n=500)
    # this order (every win, then every loss) is already the worst possible: no shuffle can beat it
    assert r["actual_max_drawdown_pct"] == pytest.approx(-200 / 10_300 * 100)
    assert r["permutation_max_drawdown_pct"]["min"] >= r["actual_max_drawdown_pct"] - 1e-9
    assert r["permutation_max_drawdown_pct"]["max"] > r["actual_max_drawdown_pct"]
    assert 0 <= r["prob_loss_pct"] <= 100
    assert sequence_tests([5.0], 10_000, 10)["note"]


def test_perturbation_and_sensitivity_run(data):
    s = load_config(env={"GRID_MODE": "ATR"})
    f = prepare_features(data, s)
    p = perturbation_runs(s, f, n=4, workers=1)
    assert p["runs"] == 4 and "p50" in p["return_pct"]
    sens = sensitivity(s, f, workers=2)
    assert set(sens) >= {"grid_spacing", "position_sizing", "max_positions", "spread_multiplier"}
    assert any("HIGH RISK" in row["value"] for row in sens["position_sizing"])
    costs = {row["value"]: row["net_pnl"] for row in sens["spread_multiplier"]}
    assert costs["x0"] >= costs["x3"]     # wider spreads never help


def test_loss_limit_sweep_really_changes_the_limit(data):
    s = load_config(env={"GRID_MODE": "ATR"})
    rows = sensitivity(s, prepare_features(data, s), workers=2)["basket_loss_limit_percent"]
    worst = {r["value"]: r["largest_basket_loss"] for r in rows}
    assert worst["5%"] < worst["1%"]      # a looser limit lets a basket lose more


def test_walk_forward_only_scores_test_windows(data):
    s = load_config(env={"GRID_MODE": "ATR"})
    wf = walk_forward(s, prepare_features(data, s), n_steps=2, workers=2)
    assert len(wf["steps"]) == 2
    for st in wf["steps"]:
        assert st["train"][1] == st["test"][0] and st["test"][0] < st["test"][1]
    assert wf["out_of_sample"]["steps"] <= 2


def test_period_stability(data):
    s = load_config(env={"GRID_MODE": "ATR"})
    rows = period_stability(s, prepare_features(data, s), freq="W-MON", workers=2)
    assert len(rows) >= 2 and all("period" in r for r in rows)


def test_comparison_lab_runs_every_preset_separately(data):
    keys = list(list_presets())
    assert {"A_fixed_grid", "B_atr_grid", "C_signal_confirmed", "D_pyramiding", "E_martingale"} <= set(keys)
    out = compare(keys, {"market": {"symbol": "XAUUSD", "timeframe": "15m"}}, candles=data, workers=2)
    rows = {r["key"]: r for r in out["rows"]}
    assert rows["E_martingale"]["high_risk"] and not rows["B_atr_grid"]["high_risk"]
    assert rows["0_control_single_position"]["avg_positions_per_basket"] == pytest.approx(1.0)
    assert "no strategy is declared superior" in out["note"]


def test_martingale_preset_is_the_only_one_allowed_to_double():
    assert load_preset("E_martingale").sizing.allow_martingale
    assert not load_preset("B_atr_grid").sizing.allow_martingale


def test_stress_suite(data):
    s = load_config(env={"GRID_MODE": "ATR"})
    out = run_stress(s, candles=data)
    names = [r["scenario"] for r in out["rows"]]
    for n in ["baseline", "strong_uptrend", "strong_downtrend", "sideways", "volatility_spike", "one_directional_move",
              "large_spread", "slippage", "repeated_reentries", "losing_streak"]:
        assert n in names
    rows = {r["scenario"]: r for r in out["rows"]}
    assert rows["large_spread"]["net_pnl"] <= rows["baseline"]["net_pnl"]
    assert rows["losing_streak"]["max_consecutive_losses"] == rows["baseline"]["baskets"] - round(
        rows["baseline"]["win_rate_pct"] * rows["baseline"]["baskets"] / 100)
    mb = out["max_basket"]
    assert mb["total_lots"] == pytest.approx(0.05) and mb["notional_usd"] > 0
