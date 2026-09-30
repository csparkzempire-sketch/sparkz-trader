"""Drawdown and daily-loss tracking on the account's equity (balance + open P&L)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class DrawdownTracker:
    peak: float
    max_drawdown_pct: float = 0.0     # worst seen, as a positive percentage
    day: date | None = None
    day_start_equity: float = 0.0

    def update(self, equity: float, ts: datetime | None = None) -> float:
        """Record an equity reading; returns the current drawdown in % (positive)."""
        if ts is not None and ts.date() != self.day:
            self.day, self.day_start_equity = ts.date(), equity
        self.peak = max(self.peak, equity)
        dd = (self.peak - equity) / self.peak * 100 if self.peak > 0 else 0.0
        self.max_drawdown_pct = max(self.max_drawdown_pct, dd)
        return dd

    def current_pct(self, equity: float) -> float:
        return (self.peak - equity) / self.peak * 100 if self.peak > 0 else 0.0

    def daily_loss_pct(self, equity: float) -> float:
        if not self.day_start_equity:
            return 0.0
        return max(0.0, (self.day_start_equity - equity) / self.day_start_equity * 100)
