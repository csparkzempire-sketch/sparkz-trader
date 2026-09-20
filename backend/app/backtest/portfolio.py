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
    position_id: int | None = None


@dataclass
class Portfolio:
    """
    Tracks account cash, equity, and zero or more concurrently open
    positions. Positions are keyed by an internally assigned integer id
    (assigned in `open()`), the same way `PaperAccountState` in
    app.paper.simulator keys live positions by symbol — this lets the
    backtest engine hold several trades open at once (up to whatever
    limit `RiskManager`/`max_simultaneous_positions` enforces) rather
    than forcing everything through a single slot.
    """

    initial_capital: float
    cash: float = field(init=False)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    open_positions: dict[int, OpenPosition] = field(default_factory=dict)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    peak_equity: float = field(init=False)
    _next_position_id: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.cash = self.initial_capital
        self.peak_equity = self.initial_capital

    @property
    def has_open_position(self) -> bool:
        """True if at least one position is currently open."""
        return len(self.open_positions) > 0

    def unrealized_pnl(self, current_price: float) -> float:
        total = 0.0
        for pos in self.open_positions.values():
            direction_sign = 1 if pos.direction == "BUY" else -1
            total += direction_sign * (current_price - pos.entry_price) * pos.size
        return total

    def equity(self, current_price: float) -> float:
        return self.cash + self.unrealized_pnl(current_price)

    def record_equity(self, timestamp: datetime, current_price: float) -> float:
        eq = self.equity(current_price)
        self.equity_curve.append((timestamp, eq))
        self.peak_equity = max(self.peak_equity, eq)
        return eq

    def open(self, position: OpenPosition) -> int:
        """Open a new position and return its position id (used to close it later)."""
        position_id = self._next_position_id
        self._next_position_id += 1
        self.open_positions[position_id] = position
        return position_id

    def close(self, position_id: int, exit_time: datetime, exit_price: float, reason: str) -> ClosedTrade:
        if position_id not in self.open_positions:
            raise RuntimeError(f"No open position with id {position_id} to close.")
        pos = self.open_positions.pop(position_id)
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
            position_id=position_id,
        )
        self.closed_trades.append(trade)
        return trade

    @property
    def current_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        current = self.equity_curve[-1][1]
        return 0.0 if self.peak_equity <= 0 else (self.peak_equity - current) / self.peak_equity
