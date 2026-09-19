"""Portfolio state tracking for the backtest engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class OpenPosition:
    direction: str  # "BUY" or "SELL"
    entry_time: datetime
    entry_price: float
    stop_price: float
    target_price: float
    size: float
    model_probability: float | None = None


@dataclass
class ClosedTrade:
    direction: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    size: float
    pnl: float
    reason: str  # "STOP", "TARGET", "SIGNAL_FLIP", "END_OF_DATA"
    model_probability: float | None = None


@dataclass
class Portfolio:
    initial_capital: float
    cash: float = field(init=False)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    open_position: OpenPosition | None = None
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    peak_equity: float = field(init=False)

    def __post_init__(self) -> None:
        self.cash = self.initial_capital
        self.peak_equity = self.initial_capital

    def unrealized_pnl(self, current_price: float) -> float:
        if self.open_position is None:
            return 0.0
        pos = self.open_position
        direction_sign = 1 if pos.direction == "BUY" else -1
        return direction_sign * (current_price - pos.entry_price) * pos.size

    def equity(self, current_price: float) -> float:
        return self.cash + self.unrealized_pnl(current_price)

    def record_equity(self, timestamp: datetime, current_price: float) -> float:
        eq = self.equity(current_price)
        self.equity_curve.append((timestamp, eq))
        self.peak_equity = max(self.peak_equity, eq)
        return eq

    def open(self, position: OpenPosition) -> None:
        if self.open_position is not None:
            raise RuntimeError("Cannot open a new position while one is already open (max_simultaneous_positions=1 assumed here).")
        self.open_position = position

    def close(self, exit_time: datetime, exit_price: float, reason: str) -> ClosedTrade:
        if self.open_position is None:
            raise RuntimeError("No open position to close.")
        pos = self.open_position
        direction_sign = 1 if pos.direction == "BUY" else -1
        pnl = direction_sign * (exit_price - pos.entry_price) * pos.size
        self.cash += pnl

        trade = ClosedTrade(
            direction=pos.direction,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            stop_price=pos.stop_price,
            target_price=pos.target_price,
            size=pos.size,
            pnl=pnl,
            reason=reason,
            model_probability=pos.model_probability,
        )
        self.closed_trades.append(trade)
        self.open_position = None
        return trade

    @property
    def current_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        current = self.equity_curve[-1][1]
        return 0.0 if self.peak_equity <= 0 else (self.peak_equity - current) / self.peak_equity
