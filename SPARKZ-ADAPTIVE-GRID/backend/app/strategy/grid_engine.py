"""
Grid / re-entry engine: where the next position of an open basket may be added.

Averaging grids (FIXED, LINEAR, MARTINGALE sizing) add when price moves
AGAINST the basket; pyramiding (PYRAMID sizing) adds only when price moves
IN FAVOUR. The distance from the previous entry:

  PRICE             price_step + step_growth x (positions - 1)     e.g. 1.00, 2.00, 3.00
  ATR               ATR x atr_multiplier
  SIGNAL_CONFIRMED  ATR x atr_multiplier, and only while the analysis still backs the direction
  NONE              no adds: one position per basket

The ATR used is the one from the last CLOSED bar, so the level for bar N is
fixed before bar N trades. Whether an add is actually allowed is then up to
the risk engine (max positions, exposure, margin, daily loss, halts).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import GridCfg, GridMode, SizingMode


@dataclass
class AddLevel:
    price: float          # mid price that triggers the add
    distance: float       # from the previous entry's reference price
    adverse: bool         # True for averaging grids, False for pyramiding


def grid_distance(n_positions: int, atr_prev: float, cfg: GridCfg) -> float | None:
    if cfg.mode == GridMode.NONE:
        return None
    if cfg.mode == GridMode.PRICE:
        return cfg.price_step + cfg.step_growth * max(n_positions - 1, 0)
    if atr_prev is None or not atr_prev > 0:
        return None
    return atr_prev * cfg.atr_multiplier


def next_add_level(direction: str, last_ref_price: float, n_positions: int, atr_prev: float,
                   grid: GridCfg, sizing_mode: SizingMode) -> AddLevel | None:
    d = grid_distance(n_positions, atr_prev, grid)
    if d is None:
        return None
    adverse = sizing_mode != SizingMode.PYRAMID
    sign = 1 if direction == "BUY" else -1
    # Averaging: a BUY basket adds lower, a SELL basket higher. Pyramiding: the opposite.
    price = last_ref_price - sign * d if adverse else last_ref_price + sign * d
    return AddLevel(price=price, distance=d, adverse=adverse)
