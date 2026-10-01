"""
Fill engine: the price a simulated order actually gets. Never a perfect fill.

Opening a BUY (or closing a SELL basket) buys at the ASK; opening a SELL (or
closing a BUY basket) sells at the BID. Slippage is then added AGAINST the
trade on every fill. Commission is charged per lot per side.

  buy fill  = mid + spread/2 + slippage
  sell fill = mid - spread/2 - slippage

Spread: execution.spread_override, else the tick's own spread (live providers),
else the instrument default; then x spread_multiplier. Slippage:
execution.slippage, else the instrument default; then x slippage_multiplier.

Reference mid: a level-triggered order (grid add, target, loss limit) is priced
from its trigger level when price moved there continuously, and from the
current tick when price jumped past it (a gap, or a discrete live poll), which
is what a real stop or market order would get.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import ExecutionCfg
from app.market.instruments import Instrument


@dataclass
class FillEngine:
    inst: Instrument
    cfg: ExecutionCfg

    def spread(self, quoted: float | None = None) -> float:
        if self.cfg.spread_override is not None:
            base = self.cfg.spread_override
        elif quoted is not None and quoted > 0:
            base = quoted
        else:
            base = self.inst.spread
        return base * self.cfg.spread_multiplier

    def slippage(self) -> float:
        base = self.cfg.slippage if self.cfg.slippage is not None else self.inst.slippage
        return base * self.cfg.slippage_multiplier

    def half_cost(self, quoted_spread: float | None = None) -> float:
        """Price distance between mid and an executed price, one side."""
        return self.spread(quoted_spread) / 2 + self.slippage()

    def price(self, side: str, mid: float, quoted_spread: float | None = None) -> float:
        h = self.half_cost(quoted_spread)
        return mid + h if side == "BUY" else mid - h

    def open_price(self, direction: str, mid: float, quoted_spread: float | None = None) -> float:
        return self.price(direction, mid, quoted_spread)

    def close_price(self, direction: str, mid: float, quoted_spread: float | None = None) -> float:
        return self.price("SELL" if direction == "BUY" else "BUY", mid, quoted_spread)

    def mid_for_close_price(self, direction: str, exit_price: float, quoted_spread: float | None = None) -> float:
        """Inverse of close_price: the mid at which closing the basket fills at `exit_price`."""
        h = self.half_cost(quoted_spread)
        return exit_price + h if direction == "BUY" else exit_price - h

    def commission(self, lots: float) -> float:
        return self.cfg.commission_per_lot_side * lots
