"""
Basket manager: a basket is every position opened in one trading cycle,
managed and closed together.

P&L conventions (all in USD):
- entry prices are FILL prices (spread and slippage included);
- a basket's P&L at a given exit price is what closing everything at that
  price would realize, minus all commissions (entries and exits);
- the exit price is the executable one: the bid for a BUY basket, the ask
  for a SELL basket (see backtest/execution.py).

For USDJPY the JPY P&L is converted at a reference USDJPY rate (the bar's
open), which keeps P&L linear in price within a bar. The error this adds
is a fraction of a percent of the P&L.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime

from app.config import Settings, TargetMode
from app.data.instruments import Instrument


@dataclass
class Position:
    seq: int
    direction: str
    lots: float
    entry_time: datetime
    entry_price: float        # fill
    ref_price: float          # mid price that triggered it (the grid measures from here)
    commission: float = 0.0   # paid at entry


@dataclass
class Basket:
    uid: str
    direction: str
    opened_at: datetime
    open_index: int
    start_equity: float
    loss_limit_usd: float
    target_usd: float | None          # money targets; None for the ATR target
    target_distance: float | None     # ATR target: price distance beyond the average entry
    atr_at_open: float
    regime: str
    vol_regime: str
    positions: list[Position] = field(default_factory=list)
    mae: float = 0.0                  # worst unrealized P&L seen (<= 0)
    mfe: float = 0.0                  # best unrealized P&L seen (>= 0)
    max_notional: float = 0.0
    max_margin: float = 0.0
    last_entry_index: int = 0
    adds_blocked: str | None = None   # why the grid stopped adding, if it did

    def clone(self) -> "Basket":
        """Copy for a what-if path. Positions never change once opened, so the list is copied, not them."""
        c = copy.copy(self)
        c.positions = list(self.positions)
        return c

    @property
    def sign(self) -> int:
        return 1 if self.direction == "BUY" else -1

    @property
    def n(self) -> int:
        return len(self.positions)

    @property
    def total_lots(self) -> float:
        return sum(p.lots for p in self.positions)

    @property
    def avg_entry(self) -> float:
        lots = self.total_lots
        return sum(p.entry_price * p.lots for p in self.positions) / lots if lots else 0.0

    @property
    def last_ref_price(self) -> float:
        return self.positions[-1].ref_price

    def usd_per_unit(self, inst: Instrument, fx: float) -> float:
        return inst.usd_per_price_unit(self.total_lots, fx)

    def commissions(self, exit_commission_per_lot: float) -> float:
        return sum(p.commission for p in self.positions) + exit_commission_per_lot * self.total_lots

    def pnl(self, exit_price: float, inst: Instrument, fx: float, exit_commission_per_lot: float = 0.0) -> float:
        k = self.usd_per_unit(inst, fx)
        return self.sign * k * (exit_price - self.avg_entry) - self.commissions(exit_commission_per_lot)

    def exit_price_for_pnl(self, pnl_usd: float, inst: Instrument, fx: float, exit_commission_per_lot: float = 0.0) -> float:
        """The exit price at which closing the whole basket realizes `pnl_usd`."""
        k = self.usd_per_unit(inst, fx)
        return self.avg_entry + self.sign * (pnl_usd + self.commissions(exit_commission_per_lot)) / k

    def target_exit_price(self, inst: Instrument, fx: float, exit_commission_per_lot: float = 0.0) -> float:
        if self.target_distance is not None:
            return self.avg_entry + self.sign * self.target_distance
        return self.exit_price_for_pnl(self.target_usd, inst, fx, exit_commission_per_lot)

    def stop_exit_price(self, inst: Instrument, fx: float, exit_commission_per_lot: float = 0.0) -> float:
        return self.exit_price_for_pnl(-self.loss_limit_usd, inst, fx, exit_commission_per_lot)

    def status(self, exit_price: float, inst: Instrument, fx: float, exit_commission_per_lot: float = 0.0) -> dict:
        """The dashboard's BASKET STATUS block."""
        pnl = self.pnl(exit_price, inst, fx, exit_commission_per_lot)
        target_price = self.target_exit_price(inst, fx, exit_commission_per_lot)
        target_usd = self.target_usd if self.target_usd is not None else \
            self.pnl(target_price, inst, fx, exit_commission_per_lot)
        return {
            "basket_id": self.uid, "direction": self.direction, "positions": self.n,
            "total_lots": round(self.total_lots, 4), "average_entry": self.avg_entry, "current_price": exit_price,
            "basket_pnl": pnl, "basket_target": target_usd, "target_price": target_price,
            "stop_price": self.stop_exit_price(inst, fx, exit_commission_per_lot),
            "loss_limit": self.loss_limit_usd, "distance_to_target": target_price - exit_price,
            "mae": self.mae, "mfe": self.mfe, "opened_at": self.opened_at.isoformat(),
            "regime_at_open": self.regime, "adds_blocked": self.adds_blocked,
            "entries": [{**asdict(p), "entry_time": p.entry_time.isoformat()} for p in self.positions],
        }


def basket_limits(settings: Settings, equity: float, atr: float) -> tuple[float, float | None, float | None]:
    """(loss limit USD, money target USD or None, ATR target distance or None) for a new basket.

    The loss limit is the tighter of stop.max_basket_loss_percent and risk.risk_per_cycle.
    The RISK_REWARD target is risk_reward x that loss limit.
    """
    loss_pct = min(settings.stop.max_basket_loss_percent, settings.risk.risk_per_cycle * 100)
    loss = equity * loss_pct / 100
    t = settings.target
    if t.mode == TargetMode.FIXED:
        return loss, t.fixed_usd, None
    if t.mode == TargetMode.PERCENT:
        return loss, equity * t.percent / 100, None
    if t.mode == TargetMode.RISK_REWARD:
        return loss, loss * t.risk_reward, None
    return loss, None, atr * t.atr_multiplier


@dataclass
class CompletedBasketRecord:
    uid: str
    direction: str
    opened_at: datetime
    closed_at: datetime
    positions: int
    total_lots: float
    avg_entry: float
    exit_price: float
    pnl: float
    pnl_pct: float
    mae: float
    mfe: float
    bars_held: int
    regime: str
    vol_regime: str
    close_reason: str      # TARGET, BASKET_STOP, TIME_STOP, ACCOUNT_DRAWDOWN, END_OF_DATA
    max_notional: float
    max_margin: float
    start_equity: float
    entries: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["opened_at"], d["closed_at"] = self.opened_at.isoformat(), self.closed_at.isoformat()
        return d
