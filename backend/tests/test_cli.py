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
