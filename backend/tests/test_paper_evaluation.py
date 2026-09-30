"""Tests for app.paper.evaluation: paper results judged against fixed backtest targets."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.paper.evaluation import MIN_TRADES, compute_norms, compute_targets, evaluate, reset_targets, targets_history
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


def _account_with_targets(candles):
    s = init_state(PaperRunConfig("t", "EURUSD=X", "1h", "baseline_long_only", 10_000))
    s.targets = compute_targets(candles, "EURUSD=X", "1h", "baseline_long_only")
    s.norms = compute_norms(candles, "EURUSD=X", "1h", "baseline_long_only")
    return s


def test_reset_recomputes_over_the_same_window_and_keeps_the_old_targets(synthetic_ohlcv):
    s = _account_with_targets(synthetic_ohlcv.iloc[:1000])
    old, old_norms = s.targets, s.norms
    new = reset_targets(s, synthetic_ohlcv, "backtester fix")  # later bars exist but are left out
    assert new["source"] == old["source"]
    assert new["backtest_trades"] == old["backtest_trades"]  # same engine here, so same figures
    assert new["reset_reason"] == "backtester fix" and new["previous"] == old
    assert s.targets is new and s.norms["previous"] == old_norms


def test_the_window_is_rebuilt_with_its_warm_up(synthetic_ohlcv):
    t = compute_targets(synthetic_ohlcv.iloc[200:1000], "EURUSD=X", "1h", "baseline_long_only")
    hist = targets_history(synthetic_ohlcv, t, "1h", "baseline_long_only")
    assert hist is not None
    assert compute_targets(hist, "EURUSD=X", "1h", "baseline_long_only")["source"] == t["source"]


def test_reset_refuses_a_window_the_data_no_longer_covers(synthetic_ohlcv):
    s = _account_with_targets(synthetic_ohlcv.iloc[:1000])
    old = s.targets
    with pytest.raises(ValueError, match="--new-window"):
        reset_targets(s, synthetic_ohlcv.iloc[500:], "fix")
    assert s.targets is old
    new = reset_targets(s, synthetic_ohlcv.iloc[500:], "fix", new_window=True)
    assert new["source"] != old["source"] and new["previous"] == old


def test_reset_needs_a_reason_and_existing_targets(synthetic_ohlcv):
    s = _account_with_targets(synthetic_ohlcv)
    with pytest.raises(ValueError, match="reason"):
        reset_targets(s, synthetic_ohlcv, "  ")
    s.targets = None
    with pytest.raises(ValueError, match="--set-targets"):
        reset_targets(s, synthetic_ohlcv, "fix")


def test_cli_reset_targets(tmp_path, monkeypatch, synthetic_ohlcv, capsys):
    import app.cli as cli
    import app.paper.runner as runner

    monkeypatch.setattr(runner, "PAPER_DIR", tmp_path)
    monkeypatch.setattr(cli, "_load_market_data", lambda sym, tf, use_cached=False: synthetic_ohlcv.copy())
    args = argparse.Namespace(account="t", symbol="EURUSD=X", timeframe="1h", strategy="baseline_long_only",
                              starting_balance=10_000, use_cached=False, resume=False, set_targets=False,
                              explicit=True)
    cli.cmd_paper_trade(args)
    first = runner.load_state(tmp_path / "t.json").targets
    capsys.readouterr()

    args.reset_targets, args.reason = True, None
    with pytest.raises(SystemExit):
        cli.cmd_paper_trade(args)
    assert runner.load_state(tmp_path / "t.json").targets == first

    args.reason = "backtester fix"
    cli.cmd_paper_trade(args)
    t = runner.load_state(tmp_path / "t.json").targets
    assert t["previous"] == first and t["reset_reason"] == "backtester fix"
    assert "Targets reset (backtester fix)" in capsys.readouterr().out
    assert evaluate(runner.load_state(tmp_path / "t.json"))["targets_reset_reason"] == "backtester fix"


def test_older_targets_are_cut_where_they_were_set(synthetic_ohlcv):
    """Targets saved before "last_bar" existed: the window ends at the bars closed at set_at."""
    t = compute_targets(synthetic_ohlcv.iloc[:1000], "EURUSD=X", "1h", "baseline_long_only")
    last = pd.Timestamp(t.pop("last_bar"))
    t["set_at"] = (last + pd.Timedelta(hours=1, minutes=5)).isoformat()
    hist = targets_history(synthetic_ohlcv, t, "1h", "baseline_long_only")
    assert pd.Timestamp(hist["timestamp"].iloc[-1]) == last
