"""Tests for app.paper.norms: live bad patches compared with the worst stretches of the backtest."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.paper.evaluation import compute_norms, targets_end
from app.paper.norms import _limit_days, _losing_runs, _max_in_week, _worst_week_pct, norms_status
from app.paper.runner import PaperRunConfig, init_state, load_state, save_state
from app.paper.simulator import PaperTradeRecord

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
NORMS = {"set_at": "x", "days": 700, "trades": 900, "max_losing_streak": 5,
         "losing_streaks": {"1": 100, "2": 40, "3": 20, "4": 6, "5": 2},
         "daily_limit_pct": 3.0, "daily_limit_days": 50, "max_daily_limit_days_in_week": 2, "worst_week_pct": -10.0}


def _state(pnls, gap_hours=1):
    s = init_state(PaperRunConfig("t", "BTC-USD", "1h", "baseline", 10_000))
    s.norms = dict(NORMS)
    for i, pnl in enumerate(pnls):
        opened = T0 + timedelta(hours=gap_hours * i)
        s.account.trade_history.append(PaperTradeRecord(
            symbol="BTC-USD", direction="BUY", entry_price=1.0, exit_price=1.0, size=1.0, pnl=pnl,
            opened_at=opened, closed_at=opened + timedelta(minutes=30), reason="STOP" if pnl <= 0 else "TARGET"))
    return s


def _item(result, name):
    return next(i for i in result["items"] if i["name"] == name)


def test_losing_runs_and_limit_days():
    assert _losing_runs([-1, -1, 2, -1, 0, -3, 5]) == [2, 3]
    closes = [(T0, -150.0), (T0 + timedelta(hours=1), -160.0), (T0 + timedelta(days=1), -100.0)]
    days = _limit_days(closes, 10_000, 0.03)
    assert days == [pd.Timestamp(T0)]  # 3.1% on day one; 1% on day two
    assert _max_in_week([pd.Timestamp(T0), pd.Timestamp(T0 + timedelta(days=3)), pd.Timestamp(T0 + timedelta(days=9))]) == 2


def test_worst_week_uses_the_start_for_short_histories():
    idx = pd.to_datetime([T0, T0 + timedelta(hours=1), T0 + timedelta(hours=2)])
    assert round(_worst_week_pct(pd.Series([10_000, 9_900, 9_700], index=idx)), 2) == -3.0


def test_a_normal_bad_patch_is_within_range():
    s = _state([-100, -100, -100])
    result = norms_status(s)
    assert result["verdict"] == "Within backtest range"
    streak = _item(result, "Losing streak")
    assert streak["live"] == "3 now, longest 3" and "28 runs of 3+ losses" in streak["note"]


def test_worse_than_anything_in_the_backtest_is_flagged():
    s = _state([-100] * 6)  # streak 6 > 5, and a 3% day
    s.norms["max_daily_limit_days_in_week"] = 0
    result = norms_status(s)
    assert _item(result, "Losing streak")["status"] == "outside"
    assert _item(result, "Daily loss limit")["status"] == "outside"
    assert result["verdict"].startswith("Outside backtest range: Losing streak, Daily loss limit")

    big = _state([-600, -600])  # -12% inside a week, beyond the backtest's -10%
    assert _item(norms_status(big), "Worst week")["status"] == "outside"


def test_accounts_without_norms_have_no_check():
    s = _state([])
    s.norms = None
    assert norms_status(s) is None


def test_norms_survive_save_and_load(tmp_path):
    s = _state([-100])
    path = save_state(s, tmp_path / "t.json")
    assert load_state(path).norms == NORMS


def test_compute_norms_backtests_the_same_setup(synthetic_ohlcv):
    n = compute_norms(synthetic_ohlcv, "EURUSD=X", "1h", "baseline_long_only")
    assert n["trades"] > 0 and n["max_losing_streak"] >= 1 and n["worst_week_pct"] <= 0
    assert sum(int(k) * v for k, v in n["losing_streaks"].items()) <= n["trades"]


def test_targets_end_reads_the_backtest_window():
    assert targets_end({"source": "backtest 2021-10-13 to 2026-09-29"}) == pd.Timestamp("2026-09-29", tz="UTC")
    assert targets_end({"source": "test"}) is None


def test_cli_adds_norms_to_existing_accounts_without_touching_targets(tmp_path, monkeypatch, synthetic_ohlcv, capsys):
    import app.cli as cli
    import app.paper.runner as runner

    monkeypatch.setattr(runner, "PAPER_DIR", tmp_path)
    monkeypatch.setattr(cli, "_load_market_data", lambda sym, tf, use_cached=False: synthetic_ohlcv.copy())
    args = argparse.Namespace(account="t", symbol="EURUSD=X", timeframe="1h", strategy="baseline_long_only",
                              starting_balance=10_000, use_cached=False, resume=False, set_targets=False,
                              explicit=True)
    cli.cmd_paper_trade(args)
    state = runner.load_state(tmp_path / "t.json")
    targets = state.targets
    assert state.norms and "Normal-losses check:" in capsys.readouterr().out

    # An account created before this check existed: norms are filled in, targets stay as they were.
    state.norms = None
    runner.save_state(state, tmp_path / "t.json")
    cli.cmd_paper_trade(args)
    again = runner.load_state(tmp_path / "t.json")
    assert again.norms and again.targets == targets
    assert "Normal-losses reference set" in capsys.readouterr().out
