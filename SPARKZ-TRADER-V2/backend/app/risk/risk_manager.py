"""
Risk manager: a pre-trade gate between the strategy and execution.

Strategy -> OrderIntent -> RiskManager.check -> ExecutionAdapter

Every OPEN_BASKET and ADD_POSITION intent must pass; CLOSE intents are never
blocked (reducing risk is always allowed). Refusal codes, in check order:

  STOPPED          emergency stop or account-drawdown halt active
  DATA_STALE       market data is stale (no new risk on old prices)
  POSITION_LIMIT   basket already holds max_positions (hard ceiling in config)
  BASKET_DRAWDOWN  basket floating loss beyond max_basket_drawdown_usd: stop adding
  DAILY_LOSS       today's loss >= max_daily_loss_percent
  COOLDOWN         new baskets only, during the post-close cooldown
  EXPOSURE         notional after the fill / equity > max_exposure_leverage
  MARGIN           margin after the fill / equity > max_margin_usage_percent
  NO_EQUITY        equity at or below zero

`check_account` runs on every tick: at max_account_drawdown_percent below the
equity peak it returns CLOSE_AND_HALT, and the robot closes the basket and stops
(fail closed) until a person resumes it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import HARD_MAX_POSITIONS, Settings
from app.execution.order_intent import IntentType, OrderIntent
from app.market.instruments import Instrument
from app.risk.drawdown import DrawdownTracker
from app.risk.exposure import leverage
from app.risk.margin import margin_usage_pct


@dataclass
class RiskDecision:
    allowed: bool
    code: str = "OK"
    detail: str = ""


@dataclass
class RiskContext:
    equity: float
    price: float
    basket_positions: int
    basket_lots: float
    basket_floating_pnl: float
    bar: int
    cooldown_until_bar: int
    stopped: str | None
    data_ok: bool


class RiskManager:
    def __init__(self, settings: Settings, inst: Instrument, tracker: DrawdownTracker):
        self.s, self.inst, self.dd = settings, inst, tracker

    @property
    def max_positions(self) -> int:
        return min(self.s.risk.max_positions, HARD_MAX_POSITIONS)

    def check(self, intent: OrderIntent, ctx: RiskContext) -> RiskDecision:
        if intent.type == IntentType.CLOSE_BASKET:
            return RiskDecision(True)
        r = self.s.risk
        if ctx.stopped:
            return RiskDecision(False, "STOPPED", ctx.stopped)
        if not ctx.data_ok:
            return RiskDecision(False, "DATA_STALE", "market data is stale: no new positions")
        if intent.type == IntentType.ADD_POSITION:
            if ctx.basket_positions >= self.max_positions:
                return RiskDecision(False, "POSITION_LIMIT", f"basket holds {ctx.basket_positions} of max {self.max_positions}")
            if r.max_basket_drawdown_usd is not None and -ctx.basket_floating_pnl >= r.max_basket_drawdown_usd:
                return RiskDecision(False, "BASKET_DRAWDOWN",
                                    f"floating loss {-ctx.basket_floating_pnl:.2f} >= {r.max_basket_drawdown_usd:.2f} USD")
        if ctx.equity <= 0:
            return RiskDecision(False, "NO_EQUITY", "equity at or below zero")
        if self.dd.daily_loss_pct(ctx.equity) >= r.max_daily_loss_percent:
            return RiskDecision(False, "DAILY_LOSS", f"daily loss {self.dd.daily_loss_pct(ctx.equity):.2f}% "
                                f">= {r.max_daily_loss_percent}%")
        if intent.type == IntentType.OPEN_BASKET and ctx.bar < ctx.cooldown_until_bar:
            return RiskDecision(False, "COOLDOWN", f"cooldown until bar {ctx.cooldown_until_bar}")
        lots_after = ctx.basket_lots + intent.lots
        lev = leverage(lots_after, ctx.price, ctx.equity, self.inst)
        if lev > r.max_exposure_leverage:
            return RiskDecision(False, "EXPOSURE", f"leverage would be {lev:.1f}x > {r.max_exposure_leverage}x")
        mu = margin_usage_pct(lots_after, ctx.price, ctx.equity, self.inst)
        if mu > r.max_margin_usage_percent:
            return RiskDecision(False, "MARGIN", f"margin use would be {mu:.1f}% > {r.max_margin_usage_percent}%")
        return RiskDecision(True)

    def check_account(self, equity: float) -> str | None:
        if self.dd.current_pct(equity) >= self.s.risk.max_account_drawdown_percent:
            return "CLOSE_AND_HALT"
        return None
