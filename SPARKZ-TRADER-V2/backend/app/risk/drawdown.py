"""Account drawdown: from the equity peak, plus the UTC day's starting equity for daily-loss limits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class DrawdownTracker:
    peak: float
    max_drawdown_pct: float = 0.0
    max_drawdown_usd: float = 0.0
    day: date | None = None
    day_start_equity: float | None = None

    def update(self, equity: float, today: date | None = None) -> None:
        if today is not None and today != self.day:
            self.day, self.day_start_equity = today, equity
        self.peak = max(self.peak, equity)
        dd = self.peak - equity
        self.max_drawdown_usd = max(self.max_drawdown_usd, dd)
        self.max_drawdown_pct = max(self.max_drawdown_pct, self.current_pct(equity))

    def current_pct(self, equity: float) -> float:
        return (self.peak - equity) / self.peak * 100 if self.peak > 0 else 0.0

    def daily_pnl(self, equity: float) -> float:
        return equity - self.day_start_equity if self.day_start_equity is not None else 0.0

    def daily_loss_pct(self, equity: float) -> float:
        if not self.day_start_equity:
            return 0.0
        return max(0.0, (self.day_start_equity - equity) / self.day_start_equity * 100)
