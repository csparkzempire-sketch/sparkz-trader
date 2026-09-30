"""
Account risk engine. Every new position, the first of a basket or an add,
must pass `check_open` / `check_add` first; a strategy signal never
overrides a refusal.

Refusals, checked in this order:
  HALTED            account drawdown limit hit; nothing opens until reset
  POSITION_LIMIT    basket already holds max_positions (hard ceiling HARD_MAX_POSITIONS)
  DAILY_LOSS        today's loss (vs equity at the UTC day start) >= max_daily_loss_percent
  COOLDOWN          within cooldown_bars_after_loss bars of a losing basket (new baskets only)
  EXPOSURE          notional after the fill / equity > max_exposure_leverage
  MARGIN            margin after the fill / equity > max_margin_usage_percent
  NO_EQUITY         equity at or below zero

`check_account` runs every bar: at max_account_drawdown_percent below the
equity peak it says CLOSE_AND_HALT, and the engine closes the basket and
stops trading (fail closed) until `reset_halt` is called deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import HARD_MAX_POSITIONS, Settings
from app.data.instruments import Instrument
from app.risk.drawdown import DrawdownTracker
from app.risk.exposure import leverage, margin_usage_pct


@dataclass
class RiskDecision:
    allowed: bool
    code: str = "OK"
    detail: str = ""


@dataclass
class RiskState:
    halted: str | None = None
    cooldown_until: int = -1          # bar index before which no new basket may start
    events: list[dict] = field(default_factory=list)


class RiskManager:
    def __init__(self, settings: Settings, inst: Instrument, state: RiskState | None = None,
                 tracker: DrawdownTracker | None = None):
        self.s, self.inst = settings, inst
        self.state = state or RiskState()
        self.dd = tracker or DrawdownTracker(peak=settings.risk.initial_capital)

    @property
    def max_positions(self) -> int:
        return min(self.s.risk.max_positions, HARD_MAX_POSITIONS)

    def _deny(self, code: str, detail: str) -> RiskDecision:
        return RiskDecision(False, code, detail)

    def _common(self, lots_after: float, price: float, equity: float) -> RiskDecision:
        r = self.s.risk
        if self.state.halted:
            return self._deny("HALTED", self.state.halted)
        if equity <= 0:
            return self._deny("NO_EQUITY", "equity at or below zero")
        if self.dd.daily_loss_pct(equity) >= r.max_daily_loss_percent:
            return self._deny("DAILY_LOSS", f"daily loss {self.dd.daily_loss_pct(equity):.2f}% >= {r.max_daily_loss_percent}%")
        lev = leverage(lots_after, price, equity, self.inst)
        if lev > r.max_exposure_leverage:
            return self._deny("EXPOSURE", f"leverage would be {lev:.1f}x > {r.max_exposure_leverage}x")
        mu = margin_usage_pct(lots_after, price, equity, self.inst)
        if mu > r.max_margin_usage_percent:
            return self._deny("MARGIN", f"margin use would be {mu:.1f}% > {r.max_margin_usage_percent}%")
        return RiskDecision(True)

    def check_open(self, lots: float, price: float, equity: float, bar_index: int) -> RiskDecision:
        if not self.state.halted and bar_index < self.state.cooldown_until:
            return self._deny("COOLDOWN", f"cooling down after a loss until bar {self.state.cooldown_until}")
        return self._common(lots, price, equity)

    def check_add(self, positions_now: int, lots_now: float, lots_add: float, price: float, equity: float) -> RiskDecision:
        if positions_now >= self.max_positions:
            return self._deny("POSITION_LIMIT", f"basket holds {positions_now} of max {self.max_positions}")
        return self._common(lots_now + lots_add, price, equity)

    def check_account(self, equity: float) -> str | None:
        """'CLOSE_AND_HALT' when the account drawdown limit is reached."""
        if self.state.halted:
            return None
        if self.dd.current_pct(equity) >= self.s.risk.max_account_drawdown_percent:
            return "CLOSE_AND_HALT"
        return None

    def halt(self, reason: str) -> None:
        self.state.halted = reason

    def reset_halt(self) -> None:
        """Deliberate human decision to resume after an account-drawdown halt."""
        self.state.halted = None
        self.dd.peak = 0.0  # the next equity reading becomes the new peak

    def on_basket_closed(self, pnl: float, bar_index: int) -> None:
        if pnl < 0:
            self.state.cooldown_until = bar_index + 1 + self.s.risk.cooldown_bars_after_loss
