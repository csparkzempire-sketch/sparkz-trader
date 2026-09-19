"""
Portfolio-level risk manager.

Enforces, on top of per-trade position sizing:
- maximum simultaneous open positions
- maximum daily loss (halts new trades for the rest of the day if breached)
- maximum drawdown (halts new trades if breached)
- maximum exposure (total risked capital as % of equity)
- a trading cooldown (minimum bars between trades)

This module only decides whether a NEW trade is allowed — it does not
place trades itself (see app.backtest.execution / app.paper.simulator).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.config import Settings, settings


@dataclass
class RiskState:
    """Mutable state the risk manager tracks across a session/backtest run."""

    equity: float
    peak_equity: float
    open_positions: int = 0
    current_exposure: float = 0.0  # sum of dollar_risk across open positions
    daily_loss: float = 0.0
    current_day: date | None = None
    bars_since_last_trade: int = field(default=10_000)


@dataclass(frozen=True)
class RiskCheckResult:
    allowed: bool
    reason: str


class RiskManager:
    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or settings

    def check_new_trade(self, state: RiskState, proposed_dollar_risk: float) -> RiskCheckResult:
        if state.open_positions >= self.cfg.max_simultaneous_positions:
            return RiskCheckResult(False, f"max_simultaneous_positions reached ({self.cfg.max_simultaneous_positions})")

        drawdown = 0.0 if state.peak_equity <= 0 else (state.peak_equity - state.equity) / state.peak_equity
        if drawdown >= self.cfg.max_drawdown_pct:
            return RiskCheckResult(False, f"max_drawdown_pct breached ({drawdown:.2%} >= {self.cfg.max_drawdown_pct:.2%})")

        daily_loss_pct = 0.0 if state.equity <= 0 else abs(min(state.daily_loss, 0.0)) / state.equity
        if daily_loss_pct >= self.cfg.max_daily_loss_pct:
            return RiskCheckResult(False, f"max_daily_loss_pct breached ({daily_loss_pct:.2%} >= {self.cfg.max_daily_loss_pct:.2%})")

        projected_exposure = state.current_exposure + proposed_dollar_risk
        exposure_pct = 0.0 if state.equity <= 0 else projected_exposure / state.equity
        if exposure_pct > self.cfg.max_exposure_pct:
            return RiskCheckResult(False, f"max_exposure_pct would be exceeded ({exposure_pct:.2%} > {self.cfg.max_exposure_pct:.2%})")

        if state.bars_since_last_trade < self.cfg.trading_cooldown_bars:
            return RiskCheckResult(False, f"trading_cooldown_bars active ({state.bars_since_last_trade} < {self.cfg.trading_cooldown_bars})")

        return RiskCheckResult(True, "ok")

    def status(self, state: RiskState) -> dict:
        drawdown = 0.0 if state.peak_equity <= 0 else (state.peak_equity - state.equity) / state.peak_equity
        exposure_pct = 0.0 if state.equity <= 0 else state.current_exposure / state.equity
        daily_loss_pct = 0.0 if state.equity <= 0 else abs(min(state.daily_loss, 0.0)) / state.equity
        return {
            "equity": state.equity,
            "current_drawdown_pct": drawdown,
            "current_exposure_pct": exposure_pct,
            "daily_loss_pct": daily_loss_pct,
            "open_positions": state.open_positions,
            "risk_status": "HALTED" if drawdown >= self.cfg.max_drawdown_pct or daily_loss_pct >= self.cfg.max_daily_loss_pct else "ACTIVE",
        }
