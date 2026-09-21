"""Tests for app.cli, focused on the parts added/changed in this session:
- train-model's --lookahead-period / --target-return-threshold overrides
- the new walk-forward subcommand (previously the module existed but had
  no CLI entry point at all)
"""

from __future__ import annotations

import argparse
import json

import pytest

import app.cli as cli_module


@pytest.fixture(autouse=True)
def _fake_download(monkeypatch, synthetic_ohlcv):
    """CLI commands call download_ohlcv(symbol=..., timeframe=...) -- swap
    it for the shared synthetic fixture so no real network call happens."""
    def _fake(symbol=None, timeframe=None, **kwargs):
        return synthetic_ohlcv.copy()
    monkeypatch.setattr(cli_module, "download_ohlcv", _fake)


@pytest.fixture(autouse=True)
def _isolated_model_dir(monkeypatch, tmp_path):
    import app.ml.model_registry as registry
    monkeypatch.setattr(registry, "MODELS_DIR", tmp_path / "models")


@pytest.fixture(autouse=True)
def _isolated_reports_dir(monkeypatch, tmp_path):
    import app.backtest.report as report_mod
    monkeypatch.setattr(report_mod, "REPORTS_DIR", tmp_path / "reports")


@pytest.fixture
def trained_model_id(synthetic_ohlcv):
    """Train a real model against the shared synthetic fixture and return
    its model_id, so cmd_backtest's model-strategy path can be exercised
    without depending on train-model's CLI output."""
    from app.ml.dataset import build_dataset
    from app.ml.train import train_model as _train_model

    dataset = build_dataset(synthetic_ohlcv)
    result = _train_model(dataset, "logistic_regression", "TEST", "1h")
    return result.model_id


def test_train_model_default_lookahead(capsys):
    args = argparse.Namespace(
        symbol="TEST", timeframe="1h", model_type="logistic_regression",
        lookahead_period=None, target_return_threshold=None,
    )
    cli_module.cmd_train_model(args)
    out = capsys.readouterr().out
    assert "Trained model_id=" in out
    assert "lookahead_period=5 bars" in out  # the config default


def test_train_model_lookahead_override_is_applied(capsys):
    args = argparse.Namespace(
        symbol="TEST", timeframe="1h", model_type="logistic_regression",
        lookahead_period=20, target_return_threshold=0.003,
    )
    cli_module.cmd_train_model(args)
    out = capsys.readouterr().out
    assert "lookahead_period=20 bars, target_return_threshold=0.003" in out


def test_train_model_prints_threshold_sweep(capsys):
    args = argparse.Namespace(
        symbol="TEST", timeframe="1h", model_type="random_forest",
        lookahead_period=None, target_return_threshold=None,
    )
    cli_module.cmd_train_model(args)
    out = capsys.readouterr().out
    assert "Threshold sweep" in out
    assert '"threshold": 0.3' in out  # confirms the widened below-0.5 range made it through


def test_train_model_custom_thresholds_override(capsys):
    args = argparse.Namespace(
        symbol="TEST", timeframe="1h", model_type="random_forest",
        lookahead_period=None, target_return_threshold=None,
        thresholds="0.15,0.20,0.25",
    )
    cli_module.cmd_train_model(args)
    out = capsys.readouterr().out
    assert '"threshold": 0.15' in out
    assert '"threshold": 0.2' in out
    assert '"threshold": 0.25' in out
    assert '"threshold": 0.5' not in out  # default range must NOT also appear


def test_walk_forward_runs_and_prints_summary(capsys):
    args = argparse.Namespace(
        symbol="TEST", timeframe="1h", model_type="logistic_regression",
        train_bars=300, test_bars=100, step_bars=None,
        lookahead_period=None, target_return_threshold=None,
    )
    cli_module.cmd_walk_forward(args)
    out = capsys.readouterr().out
    assert "walk-forward windows" in out
    assert "Summary across windows" in out
    # the summary block is the last JSON object printed -- parse it back out
    summary_json = out[out.rindex("{"):out.rindex("}") + 1]
    summary = json.loads(summary_json)
    assert summary["windows"] >= 1
    assert "avg_total_return_pct" in summary
    assert "profitable_window_pct" in summary


def test_backtest_baseline_runs(capsys):
    args = argparse.Namespace(symbol="TEST", timeframe="1h", strategy="baseline")
    cli_module.cmd_backtest(args)
    out = capsys.readouterr().out
    assert "Report saved to" in out


def test_backtest_model_strategy_default_threshold_may_fire_nothing(capsys, trained_model_id):
    """
    Regression context: with the default signal_buy_threshold (0.60), a
    model can easily produce zero BUY/SELL signals over an entire dataset
    if its probabilities never climb that high -- this used to fail
    silently (the backtest would just run with an all-HOLD signal column
    and report an empty-looking result with no explanation). Now it must
    say so explicitly.
    """
    args = argparse.Namespace(
        symbol="TEST", timeframe="1h", strategy=trained_model_id,
        buy_threshold=0.99, sell_threshold=0.99,  # deliberately unreachable
        lookahead_period=None, target_return_threshold=None,
    )
    cli_module.cmd_backtest(args)
    out, err = capsys.readouterr()
    assert "0 BUY, 0 SELL" in out
    assert "zero signals fired" in err


def test_backtest_model_strategy_custom_threshold_fires_signals(capsys, trained_model_id):
    args = argparse.Namespace(
        symbol="TEST", timeframe="1h", strategy=trained_model_id,
        buy_threshold=0.3, sell_threshold=0.3,  # generous enough to fire on synthetic data
        lookahead_period=None, target_return_threshold=None,
    )
    cli_module.cmd_backtest(args)
    out = capsys.readouterr().out
    assert "buy_threshold=0.3" in out
    assert "Report saved to" in out


def test_walk_forward_too_few_bars_errors_cleanly(capsys):
    """With windows larger than the whole dataset, there should be a clear
    error, not a silent empty result or a crash."""
    args = argparse.Namespace(
        symbol="TEST", timeframe="1h", model_type="logistic_regression",
        train_bars=100_000, test_bars=100_000, step_bars=None,
        lookahead_period=None, target_return_threshold=None,
    )
    with pytest.raises(SystemExit):
        cli_module.cmd_walk_forward(args)
    err = capsys.readouterr().err
    assert "No windows produced" in err
