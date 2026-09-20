from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import BacktestRequest, BacktestResponse
from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.metrics import (
    compare_to_buy_and_hold,
    compute_metrics,
    drawdown_curve_as_records,
    equity_curve_as_records,
    monthly_returns,
    trade_distribution,
)
from app.backtest.report import generate_and_save_report
from app.data.downloader import DownloadError, download_ohlcv
from app.data.validator import DataValidationError, validate_and_clean
from app.database.database import get_session_dep
from app.database.models import Backtest as BacktestORM
from app.database.models import BacktestTrade as BacktestTradeORM
from app.features.feature_engineering import build_feature_matrix
from app.ml.model_registry import load_model_artifact
from app.ml.predict import ModelNotAvailableError
from app.strategy.rules import baseline_signal
from app.strategy.signals import signal_from_probability
from app.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.post("", response_model=BacktestResponse)
def run_backtest(req: BacktestRequest, session: Session = Depends(get_session_dep)) -> BacktestResponse:
    try:
        raw = download_ohlcv(symbol=req.symbol, timeframe=req.timeframe)
        clean, _report = validate_and_clean(raw, timeframe=req.timeframe)
    except (DownloadError, DataValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    featured = build_feature_matrix(clean)

    if req.strategy == "baseline":
        featured["signal"] = baseline_signal(featured)
        prob_col = None
    else:
        # Treat `strategy` as a model_id.
        try:
            model = load_model_artifact(req.strategy)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown strategy/model_id: {req.strategy}") from exc
        if not hasattr(model, "predict_proba"):
            raise HTTPException(status_code=400, detail=f"Model {req.strategy} does not support probability output.")

        from app.ml.dataset import add_labels
        from app.features.feature_engineering import get_feature_columns

        labeled = add_labels(featured)
        feature_cols = get_feature_columns(labeled)
        usable_mask = labeled[feature_cols].notna().all(axis=1)
        proba_up = pd_series_full_nan(len(featured))
        proba_up.loc[usable_mask[usable_mask].index] = model.predict_proba(labeled.loc[usable_mask, feature_cols])[:, 1]
        featured["probability_up"] = proba_up
        featured["signal"] = [
            signal_from_probability(p).signal if p == p else "HOLD"  # NaN check without importing math
            for p in proba_up
        ]
        prob_col = "probability_up"

    if len(featured.dropna(subset=["atr"])) < 50:
        raise HTTPException(status_code=400, detail="Insufficient historical data after indicator warmup to run a meaningful backtest.")

    bt_config = BacktestConfig(
        symbol=req.symbol,
        timeframe=req.timeframe,
        initial_capital=req.initial_capital,
        risk_per_trade=req.risk_per_trade,
        stop_atr_multiplier=req.stop_atr_multiplier,
        take_profit_r=req.take_profit_r,
        spread_pips=req.spread_pips,
        slippage_pips=req.slippage_pips,
        max_simultaneous_positions=req.max_simultaneous_positions,
    )
    engine = BacktestEngine(bt_config)
    result = engine.run(featured, signal_col="signal", probability_col=prob_col)
    metrics = compute_metrics(result.portfolio, req.timeframe)
    baseline_cmp = compare_to_buy_and_hold(featured, req.initial_capital)

    backtest_id = f"bt_{uuid.uuid4().hex[:10]}"

    try:
        report_path = generate_and_save_report(result, metrics, backtest_id, baseline_df=featured)
        logger.info(f"Report saved to {report_path}")
    except Exception as exc:  # report generation failure must never break the API response
        logger.info(f"Report generation failed (non-fatal): {exc}")

    orm = BacktestORM(
        backtest_id=backtest_id,
        symbol=req.symbol,
        timeframe=req.timeframe,
        strategy_name=req.strategy,
        config=req.model_dump(),
        start_date=result.start_time.to_pydatetime(),
        end_date=result.end_time.to_pydatetime(),
        metrics=metrics.as_dict(),
        equity_curve=equity_curve_as_records(result.portfolio),
    )
    session.add(orm)
    session.flush()
    for t in result.portfolio.closed_trades:
        session.add(
            BacktestTradeORM(
                backtest_pk=orm.id,
                direction=t.direction,
                entry_time=t.entry_time.to_pydatetime() if hasattr(t.entry_time, "to_pydatetime") else t.entry_time,
                exit_time=t.exit_time.to_pydatetime() if hasattr(t.exit_time, "to_pydatetime") else t.exit_time,
                entry_price=t.entry_price,
                exit_price=t.exit_price,
                stop_price=t.stop_price,
                target_price=t.target_price,
                position_size=t.size,
                pnl=t.pnl,
                reason=t.reason,
                model_probability=t.model_probability,
            )
        )
    session.commit()

    return BacktestResponse(
        backtest_id=backtest_id,
        symbol=req.symbol,
        timeframe=req.timeframe,
        config=req.model_dump(),
        metrics=metrics.as_dict(),
        baseline_comparison=baseline_cmp,
        equity_curve=equity_curve_as_records(result.portfolio),
        drawdown_curve=drawdown_curve_as_records(result.portfolio),
        monthly_returns=monthly_returns(result.portfolio),
        trades=trade_distribution(result.portfolio),
        warnings=result.warnings,
    )


@router.get("/{backtest_id}", response_model=BacktestResponse)
def get_backtest(backtest_id: str, session: Session = Depends(get_session_dep)) -> BacktestResponse:
    orm = session.query(BacktestORM).filter(BacktestORM.backtest_id == backtest_id).first()
    if orm is None:
        raise HTTPException(status_code=404, detail=f"Backtest {backtest_id} not found")

    trades = [
        {
            "direction": t.direction,
            "entry_time": t.entry_time.isoformat(),
            "exit_time": t.exit_time.isoformat() if t.exit_time else None,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl": t.pnl,
            "reason": t.reason,
        }
        for t in orm.trades
    ]

    return BacktestResponse(
        backtest_id=orm.backtest_id,
        symbol=orm.symbol,
        timeframe=orm.timeframe,
        config=orm.config,
        metrics=orm.metrics,
        baseline_comparison={},
        equity_curve=orm.equity_curve,
        drawdown_curve=[],
        monthly_returns=[],
        trades=trades,
        warnings=[],
    )


def pd_series_full_nan(n: int):
    import numpy as np
    import pandas as pd

    return pd.Series(np.full(n, np.nan))
