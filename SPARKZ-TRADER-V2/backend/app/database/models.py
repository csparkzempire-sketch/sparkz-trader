"""SQLite schema (SQLAlchemy). One `runs` row per paper session or backtest; everything else hangs off it."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))             # PAPER or BACKTEST
    label: Mapped[str] = mapped_column(String(200), default="")
    preset: Mapped[str] = mapped_column(String(64), default="")
    symbol: Mapped[str] = mapped_column(String(16))
    timeframe: Mapped[str] = mapped_column(String(8))
    provider: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class EventRow(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    wall_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    type: Mapped[str] = mapped_column(String(32), index=True)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class BasketRow(Base):
    __tablename__ = "baskets"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    uid: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(4))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    positions: Mapped[int] = mapped_column(Integer)
    pnl: Mapped[float] = mapped_column(Float)
    mae: Mapped[float] = mapped_column(Float)
    close_reason: Mapped[str] = mapped_column(String(32))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class FillRow(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    type: Mapped[str] = mapped_column(String(16))
    basket_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    direction: Mapped[str] = mapped_column(String(4))
    lots: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class EquityRow(Base):
    __tablename__ = "equity"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    equity: Mapped[float] = mapped_column(Float)
    balance: Mapped[float] = mapped_column(Float)
    floating: Mapped[float] = mapped_column(Float)
    positions: Mapped[int] = mapped_column(Integer)
