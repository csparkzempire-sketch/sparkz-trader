"""Per-side percentage fees (fee_bps): charged in backtests and paper fills, set per paper account."""

import argparse

import pytest

from app.backtest.execution import ExecutionCosts, apply_entry_costs, apply_exit_costs
from app.paper.evaluation import compute_targets
from app.paper.runner import PaperRunConfig, account_settings


def test_fee_is_a_percentage_of_price_on_each_side():
    c = ExecutionCosts(spread_pips=1.0, slippage_pips=0.0, commission_per_trade=0.0, pip_size=1.0, fee_bps=10)
    assert apply_entry_costs(1000.0, "BUY", c) == pytest.approx(1000 + 1 + 1.0)
    assert apply_exit_costs(1000.0, "BUY", c) == pytest.approx(1000 - 1 - 1.0)
    assert apply_entry_costs(1000.0, "SELL", c) == pytest.approx(1000 - 1 - 1.0)


def test_fees_lower_the_backtest(synthetic_ohlcv):
    plain = compute_targets(synthetic_ohlcv, "EURUSD=X", "1h", "baseline_long_only")
    cfg = account_settings(PaperRunConfig("t", "EURUSD=X", "1h", "baseline_long_only", 10_000, fee_bps=5))
    fees = compute_targets(synthetic_ohlcv, "EURUSD=X", "1h", "baseline_long_only", cfg)
    assert fees["backtest_return_pct"] < plain["backtest_return_pct"]


def test_account_settings_only_override_the_fee():
    from app.config import settings

    assert account_settings(PaperRunConfig("t", "X", "1h", "baseline", 10_000)) is settings
    cfg = account_settings(PaperRunConfig("t", "X", "1h", "baseline", 10_000, fee_bps=10))
    assert cfg.fee_bps == 10 and cfg.risk_per_trade == settings.risk_per_trade


def test_cli_creates_a_fee_account_and_old_files_still_load(tmp_path, monkeypatch, synthetic_ohlcv):
    import json

    import app.cli as cli
    import app.paper.runner as runner

    monkeypatch.setattr(runner, "PAPER_DIR", tmp_path)
    monkeypatch.setattr(cli, "_load_market_data", lambda sym, tf, use_cached=False: synthetic_ohlcv.copy())
    args = argparse.Namespace(account="f", symbol="EURUSD=X", timeframe="1h", strategy="baseline_long_only",
                              starting_balance=10_000, use_cached=False, resume=False, set_targets=False,
                              explicit=True, fee_bps=10.0)
    cli.cmd_paper_trade(args)
    assert runner.load_state(tmp_path / "f.json").config.fee_bps == 10.0

    raw = json.loads((tmp_path / "f.json").read_text())
    del raw["config"]["fee_bps"]  # a file saved before fees existed
    (tmp_path / "old.json").write_text(json.dumps(raw))
    assert runner.load_state(tmp_path / "old.json").config.fee_bps == 0.0
