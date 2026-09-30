"""Account ledger: realized balance and the per-bar equity curve."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class EquityPoint:
    ts: datetime
    balance: float
    equity: float
    drawdown_pct: float
    positions: int
    lots: float
    notional: float
    margin: float


@dataclass
class Portfolio:
    initial_capital: float
    balance: float = 0.0
    curve: list[EquityPoint] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.balance:
            self.balance = self.initial_capital

    def realize(self, pnl: float) -> None:
        self.balance += pnl

    def record(self, point: EquityPoint) -> None:
        self.curve.append(point)
