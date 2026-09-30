"""Tests for app.paper.evaluation: paper results judged against fixed backtest targets."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import pytest

from app.paper.evaluation import MIN_TRADES, compute_targets, evaluate
from app.paper.runner import PaperRunConfig, init_state
from app.paper.simulator import PaperTradeRecord

TARGETS = {"source": "test", "profit_factor": 1.3, "win_rate_pct": 40.0, "max_drawdown_pct": -20.0,
           "avg_hold_bars": 10.0, "backtest_trades": 500, "backtest_return_pct": 50.0}
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _state(pnls, hold_hours=10):
    s = init_state(PaperRunConfig("t", "BTC-USD", "1h", "baseline_long_only", 10_000))
    s.targets = dict(TARGETS)
    for i, pnl in enumerate(pnls):
        opened = T0 + timedelta(hours=100 * i)
        s.account.trade_history.append(PaperTradeRecord(
            symbol="BTC-USD", direction="BUY", entry_price=1.0, exit_price=1.0, size=1.0, pnl=pnl,
            opened_at=opened, closed_at=opened + timedelta(hours=hold_hours), reason="TARGET" if pnl > 0 else "STOP"))
    return s


def _status(result, name):
    return next(c["status"] for c in result["checks"] if c["name"] == name)


def test_nothing_is_judged_before_enough_trades():
    r = evaluate(_state([200, -100, -100]))
    assert r["verdict"] == f"Collecting trades (3/{MIN_TRADES})"
    assert {_status(r, n) for n in ("Profit factor", "Win rate", "Avg trade length")} == {"pending"}


def test_drawdown_beyond_the_backtests_fails_immediately():
    r = evaluate(_state([-1_000, -1_000, -500]))  # -25% vs a -20% backtest worst
    assert _status(r, "Max drawdown") == "fail"
    assert r["verdict"].startswith("Failing")


def test_a_run_that_matches_the_backtest_passes():
    pnls = ([250] * 4 + [-100] * 6) * 3  # 30 trades, 40% win rate, PF 1.67
    r = evaluate(_state(pnls))
    assert r["verdict"] == "Passing", r


def test_losing_or_off_profile_runs_fail_the_right_checks():
    r = evaluate(_state(([120] * 2 + [-100] * 8) * 3, hold_hours=40))  # PF 0.3, 20% wins, 4x hold
    assert _status(r, "Profit factor") == "fail"
    assert _status(r, "Win rate") == "fail"
    assert _status(r, "Avg trade length") == "fail"


def test_accounts_without_targets_say_so():
    s = _state([])
    s.targets = None
    assert evaluate(s)["verdict"] == "No targets set"


def test_compute_targets_backtests_the_same_setup(synthetic_ohlcv):
    t = compute_targets(synthetic_ohlcv, "EURUSD=X", "1h", "baseline_long_only")
    assert t["backtest_trades"] > 0
    assert 0 <= t["win_rate_pct"] <= 100 and t["max_drawdown_pct"] <= 0 and t["avg_hold_bars"] > 0


def test_cli_sets_targets_on_creation_and_never_overwrites_them(tmp_path, monkeypatch, synthetic_ohlcv, capsys):
    import app.cli as cli
    import app.paper.runner as runner

    monkeypatch.setattr(runner, "PAPER_DIR", tmp_path)
    monkeypatch.setattr(cli, "_load_market_data", lambda sym, tf, use_cached=False: synthetic_ohlcv.copy())
    args = argparse.Namespace(account="t", symbol="EURUSD=X", timeframe="1h", strategy="baseline_long_only",
                              starting_balance=10_000, use_cached=False, resume=False, set_targets=False,
                              explicit=True)
    cli.cmd_paper_trade(args)
    first = runner.load_state(tmp_path / "t.json").targets
    assert first and "Targets set" in capsys.readouterr().out

    args.set_targets = True
    cli.cmd_paper_trade(args)
    assert runner.load_state(tmp_path / "t.json").targets == first
    assert "already set" in capsys.readouterr().out
