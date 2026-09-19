"""
Report generation (spec section 32).

After each backtest, generate a report containing configuration, data
period, strategy, costs, trade count, return, drawdown, Sharpe, Sortino,
profit factor, win rate, equity curve, drawdown curve, and trade
statistics. Saved as JSON under reports/ with a unique, timestamped
filename — reports are never silently overwritten.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.backtest.engine import BacktestConfig, BacktestResult
from app.backtest.metrics import (
    PerformanceMetrics,
    compare_to_buy_and_hold,
    drawdown_curve_as_records,
    equity_curve_as_records,
    monthly_returns,
    trade_distribution,
)
from app.utils.logging import get_logger, kv
from app.utils.time import utc_now

logger = get_logger(__name__)

REPORTS_DIR = Path(__file__).resolve().parents[3] / "reports"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def build_report(
    result: BacktestResult,
    metrics: PerformanceMetrics,
    baseline_df=None,
    backtest_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the full report dict (spec section 32 contents)."""
    portfolio = result.portfolio
    cfg = result.config

    report: dict[str, Any] = {
        "report_generated_at": utc_now().isoformat(),
        "backtest_id": backtest_id,
        "configuration": asdict(cfg) if isinstance(cfg, BacktestConfig) else dict(cfg),
        "data_period": {
            "start": result.start_time.isoformat() if hasattr(result.start_time, "isoformat") else str(result.start_time),
            "end": result.end_time.isoformat() if hasattr(result.end_time, "isoformat") else str(result.end_time),
        },
        "strategy": result.signal_source,
        "costs": {
            "spread_pips": cfg.spread_pips,
            "slippage_pips": cfg.slippage_pips,
            "commission_per_trade": cfg.commission_per_trade,
        },
        "trade_count": metrics.total_trades,
        "performance": metrics.as_dict(),
        "equity_curve": equity_curve_as_records(portfolio),
        "drawdown_curve": drawdown_curve_as_records(portfolio),
        "monthly_returns": monthly_returns(portfolio),
        "trade_statistics": trade_distribution(portfolio),
        "warnings": result.warnings,
        "disclaimer": (
            "This report reflects a historical simulation only. Historical performance "
            "does not guarantee future results. No real-money trading occurred."
        ),
    }
    if baseline_df is not None:
        report["baseline_comparison"] = compare_to_buy_and_hold(baseline_df, cfg.initial_capital)

    return report


def save_report(report: dict[str, Any], backtest_id: str) -> Path:
    """
    Save a report to reports/<backtest_id>_<timestamp>.json. Refuses to
    overwrite an existing report — every backtest run gets its own file.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    path = REPORTS_DIR / f"{backtest_id}_{timestamp}.json"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing report: {path}")

    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=_json_default)

    logger.info("Saved backtest report %s", kv(backtest_id=backtest_id, path=str(path)))
    return path


def generate_and_save_report(
    result: BacktestResult,
    metrics: PerformanceMetrics,
    backtest_id: str,
    baseline_df=None,
) -> Path:
    report = build_report(result, metrics, baseline_df=baseline_df, backtest_id=backtest_id)
    return save_report(report, backtest_id)
