"""
SQLite schema (SQLAlchemy 2.0).

Tables follow the spec: MarketCandle, StrategySignal, Basket, BasketPosition,
CompletedBasket, RiskEvent, Backtest, BacktestMetric, ParameterSet,
PaperAccount, EquitySnapshot.

Rows produced by a backtest carry `backtest_id`; rows produced by paper
trading carry `paper_account_id`. Basket IDs ("BASKET-000001") are unique
within their backtest or paper account.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import DATA_DIR
from app.utils.time import utc_now

DB_PATH = DATA_DIR / "sparkz_grid.db"


class Base(DeclarativeBase):
    pass


class MarketCandle(Base):
    __tablename__ = "market_candle"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "ts"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    spread: Mapped[float | None] = mapped_column(Float, nullable=True)   # price units, when the source has it
    source: Mapped[str] = mapped_column(String(32), default="yahoo")


class ParameterSet(Base):
    __tablename__ = "parameter_set"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64), unique=True)
    config: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Backtest(Base):
    __tablename__ = "backtest"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32), default="backtest")   # backtest | stress | monte_carlo | walk_forward
    symbol: Mapped[str] = mapped_column(String(16))
    timeframe: Mapped[str] = mapped_column(String(8))
    start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bars: Mapped[int] = mapped_column(Integer, default=0)
    parameter_set_id: Mapped[int | None] = mapped_column(ForeignKey("parameter_set.id"), nullable=True)
    data_source: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)   # curves and breakdowns for the dashboard


class BacktestMetric(Base):
    __tablename__ = "backtest_metric"
    id: Mapped[int] = mapped_column(primary_key=True)
    backtest_id: Mapped[int] = mapped_column(ForeignKey("backtest.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)


class PaperAccount(Base):
    __tablename__ = "paper_account"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    symbol: Mapped[str] = mapped_column(String(16))
    timeframe: Mapped[str] = mapped_column(String(8))
    config: Mapped[dict] = mapped_column(JSON)
    state: Mapped[dict] = mapped_column(JSON, default=dict)   # engine state; open basket included
    balance: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    halted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_bar: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Basket(Base):
    """The currently open basket of a paper account (one row per basket, updated as it changes)."""
    __tablename__ = "basket"
    __table_args__ = (UniqueConstraint("paper_account_id", "basket_uid"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    paper_account_id: Mapped[int] = mapped_column(ForeignKey("paper_account.id", ondelete="CASCADE"), index=True)
    basket_uid: Mapped[str] = mapped_column(String(24))
    direction: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(8), default="OPEN")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    positions: Mapped[int] = mapped_column(Integer)
    total_lots: Mapped[float] = mapped_column(Float)
    avg_entry: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class BasketPosition(Base):
    __tablename__ = "basket_position"
    id: Mapped[int] = mapped_column(primary_key=True)
    backtest_id: Mapped[int | None] = mapped_column(ForeignKey("backtest.id", ondelete="CASCADE"), index=True, nullable=True)
    paper_account_id: Mapped[int | None] = mapped_column(ForeignKey("paper_account.id", ondelete="CASCADE"), index=True, nullable=True)
    basket_uid: Mapped[str] = mapped_column(String(24), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    direction: Mapped[str] = mapped_column(String(4))
    lots: Mapped[float] = mapped_column(Float)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(Float)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission: Mapped[float] = mapped_column(Float, default=0.0)


class CompletedBasket(Base):
    __tablename__ = "completed_basket"
    id: Mapped[int] = mapped_column(primary_key=True)
    backtest_id: Mapped[int | None] = mapped_column(ForeignKey("backtest.id", ondelete="CASCADE"), index=True, nullable=True)
    paper_account_id: Mapped[int | None] = mapped_column(ForeignKey("paper_account.id", ondelete="CASCADE"), index=True, nullable=True)
    basket_uid: Mapped[str] = mapped_column(String(24))
    direction: Mapped[str] = mapped_column(String(4))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    positions: Mapped[int] = mapped_column(Integer)
    total_lots: Mapped[float] = mapped_column(Float)
    avg_entry: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float)
    pnl_pct: Mapped[float] = mapped_column(Float)
    mae: Mapped[float] = mapped_column(Float)
    mfe: Mapped[float] = mapped_column(Float)
    bars_held: Mapped[int] = mapped_column(Integer)
    regime: Mapped[str] = mapped_column(String(16))
    vol_regime: Mapped[str] = mapped_column(String(16))
    close_reason: Mapped[str] = mapped_column(String(24))
    max_notional: Mapped[float] = mapped_column(Float, default=0.0)
    max_margin: Mapped[float] = mapped_column(Float, default=0.0)


class RiskEvent(Base):
    __tablename__ = "risk_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    backtest_id: Mapped[int | None] = mapped_column(ForeignKey("backtest.id", ondelete="CASCADE"), index=True, nullable=True)
    paper_account_id: Mapped[int | None] = mapped_column(ForeignKey("paper_account.id", ondelete="CASCADE"), index=True, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text)


class StrategySignal(Base):
    __tablename__ = "strategy_signal"
    id: Mapped[int] = mapped_column(primary_key=True)
    backtest_id: Mapped[int | None] = mapped_column(ForeignKey("backtest.id", ondelete="CASCADE"), index=True, nullable=True)
    paper_account_id: Mapped[int | None] = mapped_column(ForeignKey("paper_account.id", ondelete="CASCADE"), index=True, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    signal: Mapped[str] = mapped_column(String(8))
    regime: Mapped[str] = mapped_column(String(16))
    vol_regime: Mapped[str] = mapped_column(String(16))
    acted: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    backtest_id: Mapped[int | None] = mapped_column(ForeignKey("backtest.id", ondelete="CASCADE"), index=True, nullable=True)
    paper_account_id: Mapped[int | None] = mapped_column(ForeignKey("paper_account.id", ondelete="CASCADE"), index=True, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    balance: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    drawdown_pct: Mapped[float] = mapped_column(Float)
    open_positions: Mapped[int] = mapped_column(Integer)


_engines: dict[str, Engine] = {}


def get_engine(path: Path | str | None = None) -> Engine:
    url = f"sqlite:///{path or DB_PATH}"
    if url not in _engines:
        Path(path or DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(url, future=True)
        Base.metadata.create_all(eng)
        _engines[url] = eng
    return _engines[url]


def session_factory(path: Path | str | None = None) -> sessionmaker:
    return sessionmaker(get_engine(path), expire_on_commit=False)
