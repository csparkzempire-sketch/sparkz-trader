"""
Execution model: turns a mid price into what a trade would actually get.

Price data is treated as MID. A buy fills at the ask (mid + spread/2) and a
sell at the bid (mid - spread/2); slippage is then added AGAINST the trade on
every fill, entries and exits alike. Commission is charged per lot per side.

The spread for a bar comes from, in order: execution.spread_override, the
bar's own spread column (imported MT5 data, when use_bar_spread is on), the
instrument default. It is then scaled by spread_multiplier, which is how the
stress tests widen it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.config import ExecutionCfg
from app.data.instruments import Instrument


@dataclass
class ExecutionModel:
    inst: Instrument
    cfg: ExecutionCfg

    def spread(self, row: pd.Series | dict | None = None) -> float:
        if self.cfg.spread_override is not None:
            base = self.cfg.spread_override
        elif self.cfg.use_bar_spread and row is not None and (sp := _get(row, "spread")) is not None and sp == sp and sp > 0:
            base = float(sp)
        else:
            base = self.inst.spread
        return base * self.cfg.spread_multiplier

    def slippage(self) -> float:
        base = self.cfg.slippage_override if self.cfg.slippage_override is not None else self.inst.slippage
        return base * self.cfg.slippage_multiplier

    def entry_fill(self, direction: str, mid: float, row=None) -> float:
        half = self.spread(row) / 2 + self.slippage()
        return mid + half if direction == "BUY" else mid - half

    def exit_fill(self, direction: str, mid: float, row=None) -> float:
        """Closing a BUY sells at the bid; closing a SELL buys at the ask."""
        half = self.spread(row) / 2 + self.slippage()
        return mid - half if direction == "BUY" else mid + half

    def exit_mid_for(self, direction: str, exit_price: float, row=None) -> float:
        """Inverse of exit_fill: the mid price at which a close would fill at `exit_price`."""
        half = self.spread(row) / 2 + self.slippage()
        return exit_price + half if direction == "BUY" else exit_price - half

    def commission(self, lots: float) -> float:
        return self.cfg.commission_per_lot_side * lots


def _get(row, key):
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError):
        return None
