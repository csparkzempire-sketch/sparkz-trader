"""Open-position view for the dashboard: one row per position of the open basket."""

from __future__ import annotations

from app.market.instruments import Instrument
from app.strategy.basket_manager import Basket


def open_positions(basket: Basket | None, exit_price: float, mid: float, inst: Instrument) -> list[dict]:
    if basket is None:
        return []
    rows = []
    for p in basket.positions:
        pnl = basket.sign * inst.usd_per_price_unit(p.lots, mid) * (exit_price - p.entry_price) - p.commission
        rows.append({"basket_id": basket.uid, "seq": p.seq, "direction": p.direction, "lots": p.lots,
                     "entry_price": p.entry_price, "entry_time": p.entry_time.isoformat(), "pnl": pnl,
                     "reason": p.reason})
    return rows
