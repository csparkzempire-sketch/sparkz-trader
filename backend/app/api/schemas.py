"""Pydantic schemas shared across API routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CandleOut(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    regime: str | None = None
    ema_20: float | None = None
    ema_50: float | None = None
    ema_200: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    atr: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None


class MarketDataResponse(BaseModel):
    symbol: str
    timeframe: str
    count: int
    candles: list[CandleOut]


class LatestPriceResponse(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    close: float
    regime: str | None = None


class BacktestRequest(BaseModel):
    symbol: str = "EURUSD=X"
    timeframe: str = "1h"
    initial_capital: float = Field(10_000.0, gt=0)
    risk_per_trade: float = Field(0.01, gt=0, lt=1)
    stop_atr_multiplier: float = Field(2.0, gt=0)
    take_profit_r: float = Field(2.0, gt=0)
    spread_pips: float = Field(1.2, ge=0)
    slippage_pips: float = Field(0.3, ge=0)
    strategy: str = Field("baseline", description="'baseline' or a model_id for ML-driven signals")


class BacktestResponse(BaseModel):
    backtest_id: str
    symbol: str
    timeframe: str
    config: dict[str, Any]
    metrics: dict[str, Any]
    baseline_comparison: dict[str, Any]
    equity_curve: list[dict[str, Any]]
    drawdown_curve: list[dict[str, Any]]
    monthly_returns: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    warnings: list[str]


class TrainModelRequest(BaseModel):
    symbol: str = "EURUSD=X"
    timeframe: str = "1h"
    model_type: str = Field("random_forest", description="logistic_regression | random_forest | xgboost")
    hyperparameters: dict[str, Any] = Field(default_factory=dict)


class TrainModelResponse(BaseModel):
    model_id: str
    model_type: str
    train_period: list[str]
    validation_period: list[str]
    test_period: list[str]
    n_features: int
    classification_metrics_validation: dict[str, Any]
    classification_metrics_test: dict[str, Any]
    feature_importance: list[dict[str, Any]]
    threshold_sweep: list[dict[str, Any]]
    disclaimer: str = (
        "Classification accuracy does not imply trading profitability. "
        "Run a backtest with this model's signals before drawing conclusions."
    )


class PredictRequest(BaseModel):
    model_id: str
    symbol: str = "EURUSD=X"
    timeframe: str = "1h"


class PredictResponse(BaseModel):
    model_id: str
    symbol: str
    timestamp: datetime
    probability_up: float
    probability_down: float
    signal: str
    explanation: str


class ModelSummary(BaseModel):
    model_id: str
    model_type: str
    symbol: str
    timeframe: str
    created_at: datetime
    metrics: dict[str, Any]


class PaperStartRequest(BaseModel):
    account_name: str = "default"
    starting_balance: float = Field(10_000.0, gt=0)


class PaperAccountResponse(BaseModel):
    account_name: str
    balance: float
    equity: float
    is_active: bool
    open_positions: int


class PaperPositionOut(BaseModel):
    symbol: str
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    size: float
    opened_at: datetime


class PaperTradeOut(BaseModel):
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    opened_at: datetime
    closed_at: datetime
    reason: str
