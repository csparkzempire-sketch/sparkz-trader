"""
Basket manager: every position opened in one trading cycle belongs to one basket,
and the basket is managed and closed as a whole.

Conventions (all money in USD):
- entry prices are FILL prices (spread and slippage included);
- the basket's P&L at an exit price is what closing everything there would
  realize, minus all commissions;
- the exit price for marking is the executable one: bid for a BUY basket, ask
  for a SELL basket (plus slippage), see execution/fill_engine.py;
- for USDJPY, P&L in JPY is converted at the current price.

Recovery analysis (`recovery`) is ANALYTICAL ONLY: how far price must move to
break even, reach the target or hit the loss limit, and how much extra exposure
would be needed to pull the break-even price closer. Nothing ever adds that
exposure automatically.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from app.config import Settings, TargetMode
from app.market.instruments import Instrument


@dataclass
class Position:
    seq: int
    direction: str
    lots: float
    entry_time: datetime
    entry_price: float       # fill
    ref_price: float         # mid that triggered it (the grid measures from here)
    commission: float = 0.0
    reason: str = ""


@dataclass
class Basket:
    uid: str
    direction: str
    opened_at: datetime
    open_bar: int
    start_equity: float
    loss_limit_usd: float
    target_usd: float | None          # money targets
    target_distance: float | None     # ATR target: price distance beyond the average entry
    atr_at_open: float
    regime: str
    analysis: dict = field(default_factory=dict)
    positions: list[Position] = field(default_factory=list)
    mae: float = 0.0
    mfe: float = 0.0
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0         # largest fall from the basket's best P&L
    max_lots: float = 0.0
    max_notional: float = 0.0
    max_margin: float = 0.0
    last_add_bar: int = 0
    adds_blocked: str | None = None
    last_pnl: float = 0.0

    @property
    def sign(self) -> int:
        return 1 if self.direction == "BUY" else -1

    @property
    def n(self) -> int:
        return len(self.positions)

    @property
    def total_lots(self) -> float:
        return round(sum(p.lots for p in self.positions), 8)

    @property
    def avg_entry(self) -> float:
        lots = self.total_lots
        return sum(p.entry_price * p.lots for p in self.positions) / lots if lots else 0.0

    @property
    def last_ref_price(self) -> float:
        return self.positions[-1].ref_price

    def usd_per_unit(self, inst: Instrument, fx: float) -> float:
        return inst.usd_per_price_unit(self.total_lots, fx)

    def commissions(self, exit_comm_per_lot: float) -> float:
        return sum(p.commission for p in self.positions) + exit_comm_per_lot * self.total_lots

    def pnl(self, exit_price: float, inst: Instrument, fx: float, exit_comm_per_lot: float = 0.0) -> float:
        return self.sign * self.usd_per_unit(inst, fx) * (exit_price - self.avg_entry) - self.commissions(exit_comm_per_lot)

    def exit_price_for_pnl(self, pnl: float, inst: Instrument, fx: float, exit_comm_per_lot: float = 0.0) -> float:
        k = self.usd_per_unit(inst, fx)
        return self.avg_entry + self.sign * (pnl + self.commissions(exit_comm_per_lot)) / k

    def target_exit_price(self, inst, fx, comm=0.0) -> float:
        if self.target_distance is not None:
            return self.avg_entry + self.sign * self.target_distance
        return self.exit_price_for_pnl(self.target_usd, inst, fx, comm)

    def stop_exit_price(self, inst, fx, comm=0.0) -> float:
        return self.exit_price_for_pnl(-self.loss_limit_usd, inst, fx, comm)

    def mark(self, pnl: float, price: float, inst: Instrument) -> None:
        self.last_pnl = pnl
        self.mae, self.mfe = min(self.mae, pnl), max(self.mfe, pnl)
        self.peak_pnl = max(self.peak_pnl, pnl)
        self.max_drawdown = max(self.max_drawdown, self.peak_pnl - pnl)
        self.max_lots = max(self.max_lots, self.total_lots)
        self.max_notional = max(self.max_notional, inst.notional_usd(self.total_lots, price))
        self.max_margin = max(self.max_margin, inst.margin_usd(self.total_lots, price))

    def recovery(self, exit_price: float, mid: float, atr: float, inst: Instrument, entry_cost: float,
                 comm: float = 0.0) -> dict:
        """ANALYTICAL ONLY: distances to break-even / target / loss limit, and the extra lots that
        would be needed to pull the break-even price within 1 ATR of the current price."""
        fx = mid
        be = self.exit_price_for_pnl(0.0, inst, fx, comm)
        tgt = self.target_exit_price(inst, fx, comm)
        stop = self.stop_exit_price(inst, fx, comm)

        def move(level: float) -> dict:
            d = self.sign * (level - exit_price)   # > 0: price must move in the basket's favour
            return {"price": level, "move": d, "move_pct": d / mid * 100, "move_atr": d / atr if atr > 0 else None}

        out = {"average_entry": self.avg_entry, "current_exit_price": exit_price, "break_even": move(be),
               "target": move(tgt), "loss_limit": move(stop), "extra_lots_for_be_within_1atr": None}
        need = self.sign * (be - exit_price)
        if need > atr > 0:
            # add x lots at the current fill price c so the new average entry sits 1 ATR from the exit price
            c = mid + self.sign * entry_cost
            new_avg = exit_price + self.sign * atr
            denom = new_avg - c
            if denom * self.sign < 0 or abs(denom) < 1e-12:
                x = None
            else:
                x = self.total_lots * (self.avg_entry - new_avg) / denom
            if x is not None and x > 0:
                lots = self.total_lots + x
                out["extra_lots_for_be_within_1atr"] = {
                    "lots": round(x, 4), "total_lots_after": round(lots, 4),
                    "notional_after_usd": inst.notional_usd(lots, mid), "margin_after_usd": inst.margin_usd(lots, mid),
                    "note": "analysis only: never added automatically"}
        return out

    def status(self, exit_price: float, mid: float, atr: float, inst: Instrument, entry_cost: float,
               comm: float = 0.0) -> dict:
        pnl = self.pnl(exit_price, inst, mid, comm)
        tgt_price = self.target_exit_price(inst, mid, comm)
        target_usd = self.target_usd if self.target_usd is not None else self.pnl(tgt_price, inst, mid, comm)
        return {
            "basket_id": self.uid, "direction": self.direction, "positions": self.n,
            "total_lots": self.total_lots, "average_entry": self.avg_entry, "current_price": exit_price,
            "floating_pnl": pnl, "target_usd": target_usd, "target_price": tgt_price,
            "loss_limit_usd": self.loss_limit_usd, "stop_price": self.stop_exit_price(inst, mid, comm),
            "mae": self.mae, "mfe": self.mfe, "max_drawdown": self.max_drawdown,
            "exposure_usd": inst.notional_usd(self.total_lots, mid), "margin_usd": inst.margin_usd(self.total_lots, mid),
            "opened_at": self.opened_at.isoformat(), "regime_at_open": self.regime, "adds_blocked": self.adds_blocked,
            "recovery": self.recovery(exit_price, mid, atr, inst, entry_cost, comm),
            "entries": [{**asdict(p), "entry_time": p.entry_time.isoformat()} for p in self.positions],
        }


def basket_limits(s: Settings, equity: float, atr: float) -> tuple[float, float | None, float | None]:
    """(loss limit USD, money target USD or None, ATR target distance or None) for a new basket."""
    loss = equity * s.risk.max_basket_loss_percent / 100
    if s.risk.max_basket_loss_usd is not None:
        loss = min(loss, s.risk.max_basket_loss_usd)
    t = s.target
    if t.mode == TargetMode.FIXED_PROFIT:
        return loss, t.fixed_usd, None
    if t.mode == TargetMode.PERCENT_EQUITY:
        return loss, equity * t.percent / 100, None
    if t.mode == TargetMode.RISK_MULTIPLE:
        return loss, loss * t.risk_multiple, None
    return loss, None, atr * t.atr_multiplier


@dataclass
class CompletedBasket:
    uid: str
    direction: str
    opened_at: datetime
    closed_at: datetime
    positions: int
    total_lots: float
    max_lots: float
    avg_entry: float
    exit_price: float
    pnl: float
    pnl_pct: float
    mae: float
    mfe: float
    max_drawdown: float
    bars_held: int
    duration_minutes: float
    regime: str
    close_reason: str     # TARGET, LOSS_LIMIT, ACCOUNT_DRAWDOWN, MANUAL, END_OF_DATA
    max_notional: float
    max_margin: float
    start_equity: float
    entries: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["opened_at"], d["closed_at"] = self.opened_at.isoformat(), self.closed_at.isoformat()
        return d


class BasketManager:
    def __init__(self):
        self.current: Basket | None = None
        self.history: list[CompletedBasket] = []
        self.counter = 0

    def new_id(self) -> str:
        self.counter += 1
        return f"BASKET-{self.counter:06d}"

    def open(self, basket: Basket) -> Basket:
        if self.current is not None:
            raise RuntimeError("a basket is already open")
        self.current = basket
        return basket

    def close(self, exit_price: float, pnl: float, when: datetime, bar: int, reason: str) -> CompletedBasket:
        b = self.current
        if b is None:
            raise RuntimeError("no open basket")
        rec = CompletedBasket(
            b.uid, b.direction, b.opened_at, when, b.n, b.total_lots, b.max_lots, b.avg_entry, exit_price, pnl,
            pnl / b.start_equity * 100, b.mae, b.mfe, b.max_drawdown, max(bar - b.open_bar, 0),
            (when - b.opened_at).total_seconds() / 60, b.regime, reason, b.max_notional, b.max_margin,
            b.start_equity, [{**asdict(p), "entry_time": p.entry_time.isoformat()} for p in b.positions])
        self.history.append(rec)
        self.current = None
        return rec
