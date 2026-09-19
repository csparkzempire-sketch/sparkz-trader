from __future__ import annotations

import json

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.metrics import compute_metrics
from app.backtest.report import build_report, save_report
from app.features.feature_engineering import build_feature_matrix
from app.strategy.rules import baseline_signal


def test_build_report_contains_required_sections(synthetic_ohlcv):
    featured = build_feature_matrix(synthetic_ohlcv)
    featured["signal"] = baseline_signal(featured)

    config = BacktestConfig(symbol="TESTUSD", timeframe="1h", initial_capital=10_000)
    engine = BacktestEngine(config)
    result = engine.run(featured, signal_col="signal")
    metrics = compute_metrics(result.portfolio, "1h")

    report = build_report(result, metrics, baseline_df=featured, backtest_id="bt_test123")

    for key in [
        "configuration", "data_period", "strategy", "costs", "trade_count",
        "performance", "equity_curve", "drawdown_curve", "monthly_returns",
        "trade_statistics", "disclaimer", "baseline_comparison",
    ]:
        assert key in report

    assert report["trade_count"] == metrics.total_trades
    assert report["performance"]["total_return_pct"] == metrics.total_return_pct
    for forbidden in ["100% accurate", "risk-free", "certain buy", "certain sell"]:
        assert forbidden not in report["disclaimer"].lower()
    assert "does not guarantee future results" in report["disclaimer"].lower()


def test_save_report_writes_valid_json(synthetic_ohlcv, tmp_path, monkeypatch):
    import app.backtest.report as report_mod

    monkeypatch.setattr(report_mod, "REPORTS_DIR", tmp_path)

    featured = build_feature_matrix(synthetic_ohlcv)
    featured["signal"] = baseline_signal(featured)
    config = BacktestConfig(symbol="TESTUSD", timeframe="1h", initial_capital=10_000)
    engine = BacktestEngine(config)
    result = engine.run(featured, signal_col="signal")
    metrics = compute_metrics(result.portfolio, "1h")
    report = build_report(result, metrics, baseline_df=featured, backtest_id="bt_test456")

    path = save_report(report, "bt_test456")
    assert path.exists()

    with open(path) as f:
        loaded = json.load(f)
    assert loaded["backtest_id"] == "bt_test456"


def test_save_report_refuses_to_overwrite_existing_file(synthetic_ohlcv, tmp_path, monkeypatch):
    import app.backtest.report as report_mod
    from app.utils.time import utc_now
    import pytest

    monkeypatch.setattr(report_mod, "REPORTS_DIR", tmp_path)

    fixed_now = utc_now()
    monkeypatch.setattr(report_mod, "utc_now", lambda: fixed_now)

    expected_path = tmp_path / f"bt_dup_{fixed_now.strftime('%Y%m%dT%H%M%SZ')}.json"
    expected_path.write_text("{}")  # pre-create the exact path save_report would use

    with pytest.raises(FileExistsError):
        save_report({"backtest_id": "bt_dup"}, "bt_dup")
