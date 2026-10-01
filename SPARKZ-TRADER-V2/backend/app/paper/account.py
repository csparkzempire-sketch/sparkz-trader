"""
PAPER ACCOUNT: a virtual account. Every figure here is simulated; no money and
no broker account are involved.

  balance        starting capital + realized P&L (changes when a basket closes)
  floating_pnl   open basket's P&L at the executable exit price
  equity         balance + floating_pnl
  used_margin    margin required by open positions
  free_margin    equity - used_margin
  realized_pnl   sum of closed baskets' P&L
  daily_pnl      equity change since the start of the UTC day
  drawdown       fall from the equity peak (current and maximum)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.risk.drawdown import DrawdownTracker
from app.risk.margin import margin_level_pct

ACCOUNT_LABEL = "PAPER ACCOUNT"


@dataclass
class PaperAccount:
    initial_balance: float
    balance: float = 0.0
    floating_pnl: float = 0.0
    used_margin: float = 0.0
    realized_pnl: float = 0.0
    open_positions: int = 0
    open_lots: float = 0.0
    commissions_paid: float = 0.0
    dd: DrawdownTracker = field(default=None)      # type: ignore[assignment]
    last_update: datetime | None = None

    def __post_init__(self):
        self.balance = self.balance or self.initial_balance
        if self.dd is None:
            self.dd = DrawdownTracker(peak=self.initial_balance)

    @property
    def equity(self) -> float:
        return self.balance + self.floating_pnl

    @property
    def free_margin(self) -> float:
        return self.equity - self.used_margin

    def mark(self, floating_pnl: float, used_margin: float, positions: int, lots: float, when: datetime) -> None:
        self.floating_pnl, self.used_margin = floating_pnl, used_margin
        self.open_positions, self.open_lots = positions, lots
        self.last_update = when
        self.dd.update(self.equity, when.date())

    def realize(self, pnl: float, when: datetime) -> None:
        self.balance += pnl
        self.realized_pnl += pnl
        self.mark(0.0, 0.0, 0, 0.0, when)

    def snapshot(self) -> dict:
        eq = self.equity
        return {
            "label": ACCOUNT_LABEL, "simulated": True, "initial_balance": self.initial_balance,
            "balance": self.balance, "equity": eq, "floating_pnl": self.floating_pnl,
            "realized_pnl": self.realized_pnl, "used_margin": self.used_margin, "free_margin": self.free_margin,
            "margin_level_pct": margin_level_pct(eq, self.used_margin), "daily_pnl": self.dd.daily_pnl(eq),
            "drawdown_pct": self.dd.current_pct(eq), "max_drawdown_pct": self.dd.max_drawdown_pct,
            "max_drawdown_usd": self.dd.max_drawdown_usd, "open_positions": self.open_positions,
            "open_lots": self.open_lots, "return_pct": (eq / self.initial_balance - 1) * 100,
            "updated_at": self.last_update.isoformat() if self.last_update else None,
        }
