"""
SPARKZ TRADER command-line interface.

Usage:
    python -m app.cli download-data [--symbol EURUSD=X] [--timeframe 1h]
    python -m app.cli backtest [--symbol EURUSD=X] [--timeframe 1h] [--strategy baseline]
    python -m app.cli train-model [--symbol EURUSD=X] [--timeframe 1h] [--model-type random_forest]
    python -m app.cli evaluate-model --model-id <id>
    python -m app.cli paper-trade [--symbol EURUSD=X]
    python -m app.cli system-status
"""

from __future__ import annotations

import argparse
import json
import sys

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.metrics import compare_to_buy_and_hold, compute_metrics
from app.backtest.report import generate_and_save_report
from app.config import settings
from app.data.downloader import DownloadError, download_ohlcv
from app.data.repository import save_processed
from app.data.validator import DataValidationError, validate_and_clean
from app.features.feature_engineering import build_feature_matrix
from app.ml.dataset import LeakageError, build_dataset
from app.ml.evaluate import evaluate_classification, feature_importance
from app.ml.model_registry import load_model_artifact
from app.ml.train import train_model
from app.strategy.rules import baseline_signal
from app.utils.logging import get_logger

logger = get_logger("cli", settings.log_level)


def cmd_download_data(args) -> None:
    try:
        raw = download_ohlcv(symbol=args.symbol, timeframe=args.timeframe)
        clean, report = validate_and_clean(raw, timeframe=args.timeframe)
    except (DownloadError, DataValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    path = save_processed(clean, args.symbol, args.timeframe)
    print(f"Downloaded and saved {len(clean)} candles to {path}")
    print(json.dumps(report.as_dict(), indent=2, default=str))


def cmd_backtest(args) -> None:
    try:
        raw = download_ohlcv(symbol=args.symbol, timeframe=args.timeframe)
        clean, _ = validate_and_clean(raw, timeframe=args.timeframe)
    except (DownloadError, DataValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    featured = build_feature_matrix(clean)
    if args.strategy == "baseline":
        featured["signal"] = baseline_signal(featured)
    else:
        model = load_model_artifact(args.strategy)
        from app.features.feature_engineering import get_feature_columns
        from app.ml.dataset import add_labels
        from app.strategy.signals import signal_from_probability

        labeled = add_labels(featured)
        feature_cols = get_feature_columns(labeled)
        mask = labeled[feature_cols].notna().all(axis=1)
        featured["signal"] = "HOLD"
        proba = model.predict_proba(labeled.loc[mask, feature_cols])[:, 1]
        featured.loc[mask, "signal"] = [signal_from_probability(p).signal for p in proba]

    bt_config = BacktestConfig.from_settings(settings, args.symbol, args.timeframe)
    engine = BacktestEngine(bt_config)
    result = engine.run(featured, signal_col="signal")
    metrics = compute_metrics(result.portfolio, args.timeframe)
    baseline_cmp = compare_to_buy_and_hold(featured, bt_config.initial_capital)

    print(json.dumps(metrics.as_dict(), indent=2))
    print(json.dumps(baseline_cmp, indent=2))
    if result.warnings:
        print("WARNINGS:", result.warnings)

    import uuid as _uuid
    backtest_id = f"bt_{_uuid.uuid4().hex[:10]}"
    report_path = generate_and_save_report(result, metrics, backtest_id, baseline_df=featured)
    print(f"Report saved to {report_path}")


def cmd_train_model(args) -> None:
    try:
        raw = download_ohlcv(symbol=args.symbol, timeframe=args.timeframe)
        clean, _ = validate_and_clean(raw, timeframe=args.timeframe)
        dataset = build_dataset(clean)
    except (DownloadError, DataValidationError, LeakageError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    result = train_model(dataset, args.model_type, args.symbol, args.timeframe)
    val_metrics = evaluate_classification(result.model, dataset.X_val, dataset.y_val)
    test_metrics = evaluate_classification(result.model, dataset.X_test, dataset.y_test)
    importances = feature_importance(result.model, dataset.feature_columns)

    print(f"Trained model_id={result.model_id}")
    print("Validation metrics:", json.dumps(val_metrics.as_dict(), indent=2))
    print("Test metrics:", json.dumps(test_metrics.as_dict(), indent=2))
    print("Top features:", json.dumps(importances[:10], indent=2))


def cmd_evaluate_model(args) -> None:
    try:
        model = load_model_artifact(args.model_id)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Model {args.model_id} loaded successfully: {type(model).__name__}")


def cmd_paper_trade(args) -> None:
    print(
        "Paper trading is available via the API (POST /paper/start, /paper/positions, etc.) "
        "or programmatically via app.paper.simulator.PaperTradingSimulator. "
        "No real broker connection exists in this build."
    )


def cmd_system_status(args) -> None:
    print(json.dumps({
        "app": settings.app_name,
        "environment": settings.environment,
        "market_symbol": settings.market_symbol,
        "timeframe": settings.timeframe,
        "live_trading_enabled": settings.live_trading_enabled,
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli", description="SPARKZ TRADER CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("download-data")
    p.add_argument("--symbol", default=settings.market_symbol)
    p.add_argument("--timeframe", default=settings.timeframe)
    p.set_defaults(func=cmd_download_data)

    p = sub.add_parser("backtest")
    p.add_argument("--symbol", default=settings.market_symbol)
    p.add_argument("--timeframe", default=settings.timeframe)
    p.add_argument("--strategy", default="baseline")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("train-model")
    p.add_argument("--symbol", default=settings.market_symbol)
    p.add_argument("--timeframe", default=settings.timeframe)
    p.add_argument("--model-type", default="random_forest", dest="model_type")
    p.set_defaults(func=cmd_train_model)

    p = sub.add_parser("evaluate-model")
    p.add_argument("--model-id", required=True, dest="model_id")
    p.set_defaults(func=cmd_evaluate_model)

    p = sub.add_parser("paper-trade")
    p.set_defaults(func=cmd_paper_trade)

    p = sub.add_parser("system-status")
    p.set_defaults(func=cmd_system_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
