"""
Central configuration for SPARKZ TRADER.

All tunable parameters live here and are sourced from environment variables
(with sane defaults for local development). Nothing in this file is a secret;
secrets (broker API keys, etc.) must never be hard-coded and are only ever
read from the environment, never logged, never sent to the frontend.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel, Field


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val is not None else default


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val is not None else default


class Settings(BaseModel):
    # --- App ---
    app_name: str = "SPARKZ TRADER"
    environment: str = Field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    log_level: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    # --- Market ---
    market_symbol: str = Field(default_factory=lambda: os.getenv("MARKET_SYMBOL", "EURUSD=X"))
    timeframe: str = Field(default_factory=lambda: os.getenv("TIMEFRAME", "1h"))

    # --- Capital / Risk ---
    initial_capital: float = Field(default_factory=lambda: _get_float("INITIAL_CAPITAL", 10_000.0))
    risk_per_trade: float = Field(default_factory=lambda: _get_float("RISK_PER_TRADE", 0.01))
    max_simultaneous_positions: int = Field(default_factory=lambda: _get_int("MAX_SIMULTANEOUS_POSITIONS", 1))
    max_daily_loss_pct: float = Field(default_factory=lambda: _get_float("MAX_DAILY_LOSS_PCT", 0.03))
    max_drawdown_pct: float = Field(default_factory=lambda: _get_float("MAX_DRAWDOWN_PCT", 0.20))
    max_exposure_pct: float = Field(default_factory=lambda: _get_float("MAX_EXPOSURE_PCT", 0.5))
    trading_cooldown_bars: int = Field(default_factory=lambda: _get_int("TRADING_COOLDOWN_BARS", 0))

    # --- Stops / Targets ---
    stop_atr_multiplier: float = Field(default_factory=lambda: _get_float("STOP_ATR_MULTIPLIER", 2.0))
    take_profit_r: float = Field(default_factory=lambda: _get_float("TAKE_PROFIT_R", 2.0))

    # --- Baseline strategy thresholds ---
    rsi_period: int = Field(default_factory=lambda: _get_int("RSI_PERIOD", 14))
    rsi_buy_threshold: float = Field(default_factory=lambda: _get_float("RSI_BUY_THRESHOLD", 50.0))
    rsi_sell_threshold: float = Field(default_factory=lambda: _get_float("RSI_SELL_THRESHOLD", 50.0))
    ema_fast: int = Field(default_factory=lambda: _get_int("EMA_FAST", 20))
    ema_slow: int = Field(default_factory=lambda: _get_int("EMA_SLOW", 50))
    ema_long: int = Field(default_factory=lambda: _get_int("EMA_LONG", 200))
    atr_period: int = Field(default_factory=lambda: _get_int("ATR_PERIOD", 14))

    # --- ML ---
    lookahead_period: int = Field(default_factory=lambda: _get_int("LOOKAHEAD_PERIOD", 5))
    target_return_threshold: float = Field(default_factory=lambda: _get_float("TARGET_RETURN_THRESHOLD", 0.0005))
    train_fraction: float = Field(default_factory=lambda: _get_float("TRAIN_FRACTION", 0.70))
    validation_fraction: float = Field(default_factory=lambda: _get_float("VALIDATION_FRACTION", 0.15))
    # test_fraction is implied: 1 - train - validation
    signal_buy_threshold: float = Field(default_factory=lambda: _get_float("SIGNAL_BUY_THRESHOLD", 0.60))
    signal_sell_threshold: float = Field(default_factory=lambda: _get_float("SIGNAL_SELL_THRESHOLD", 0.60))

    # --- Backtest costs ---
    spread_pips: float = Field(default_factory=lambda: _get_float("SPREAD_PIPS", 1.2))
    slippage_pips: float = Field(default_factory=lambda: _get_float("SLIPPAGE_PIPS", 0.3))
    commission_per_trade: float = Field(default_factory=lambda: _get_float("COMMISSION_PER_TRADE", 0.0))
    pip_size: float = Field(default_factory=lambda: _get_float("PIP_SIZE", 0.0001))

    # --- Database ---
    database_url: str = Field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/sparkz_trader.db"))

    # --- Safety switch: this must be explicitly true for any live broker code to run ---
    live_trading_enabled: bool = Field(default_factory=lambda: _get_bool("LIVE_TRADING_ENABLED", False))

    # --- CORS ---
    cors_origins: list[str] = Field(default_factory=lambda: os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(","))

    @property
    def test_fraction(self) -> float:
        return round(1.0 - self.train_fraction - self.validation_fraction, 10)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
