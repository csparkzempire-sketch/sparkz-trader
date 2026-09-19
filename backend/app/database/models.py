"""SQLAlchemy ORM models for SPARKZ TRADER."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.utils.time import utc_now


class Base(DeclarativeBase):
    pass


class MarketCandle(Base):
    __tablename__ = "market_candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_candle_symbol_tf_ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    timeframe: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    regime: Mapped[str | None] = mapped_column(String, nullable=True)


class Model(Base):
    __tablename__ = "models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    model_type: Mapped[str] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String)
    timeframe: Mapped[str] = mapped_column(String)
    training_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    training_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    validation_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    validation_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    test_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    test_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    features: Mapped[list] = mapped_column(JSON)
    hyperparameters: Mapped[dict] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    artifact_path: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    predictions: Mapped[list["Prediction"]] = relationship(back_populates="model")


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_pk: Mapped[int] = mapped_column(ForeignKey("models.id"))
    symbol: Mapped[str] = mapped_column(String)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    probability_up: Mapped[float] = mapped_column(Float)
    probability_down: Mapped[float] = mapped_column(Float)
    signal: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    model: Mapped["Model"] = relationship(back_populates="predictions")


class Backtest(Base):
    __tablename__ = "backtests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backtest_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String)
    timeframe: Mapped[str] = mapped_column(String)
    strategy_name: Mapped[str] = mapped_column(String)
    config: Mapped[dict] = mapped_column(JSON)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict] = mapped_column(JSON)
    equity_curve: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    trades: Mapped[list["BacktestTrade"]] = relationship(back_populates="backtest", cascade="all, delete-orphan")


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backtest_pk: Mapped[int] = mapped_column(ForeignKey("backtests.id"))
    direction: Mapped[str] = mapped_column(String)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float] = mapped_column(Float)
    target_price: Mapped[float] = mapped_column(Float)
    position_size: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String)
    model_probability: Mapped[float | None] = mapped_column(Float, nullable=True)

    backtest: Mapped["Backtest"] = relationship(back_populates="trades")


class PaperAccount(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_name: Mapped[str] = mapped_column(String, unique=True)
    balance: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    positions: Mapped[list["PaperPosition"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    trades: Mapped[list["PaperTrade"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_pk: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"))
    symbol: Mapped[str] = mapped_column(String)
    direction: Mapped[str] = mapped_column(String)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float] = mapped_column(Float)
    target_price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    account: Mapped["PaperAccount"] = relationship(back_populates="positions")


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_pk: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"))
    symbol: Mapped[str] = mapped_column(String)
    direction: Mapped[str] = mapped_column(String)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reason: Mapped[str] = mapped_column(String)

    account: Mapped["PaperAccount"] = relationship(back_populates="trades")
