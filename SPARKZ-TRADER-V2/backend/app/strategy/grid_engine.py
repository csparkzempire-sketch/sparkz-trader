"""
Adaptive grid (re-entry) engine: where an open basket may add its next position.

Averaging grids (FIXED, LINEAR, MULTIPLIER sizing) add when price moves AGAINST
the basket; PYRAMID adds only when price moves IN FAVOUR. Distance from the
previous entry's reference price:

  MODE A  FIXED             grid.distance price units (GRID_DISTANCE)
  MODE B  ATR               ATR x grid.atr_multiplier (GRID_ATR_MULTIPLIER)
  MODE C  SIGNAL_CONFIRMED  ATR x multiplier, and only while the basket's original
                            thesis still holds (entry_engine.thesis_holds). Once it
                            breaks, the basket stops adding for good.
          NONE              no adds (one position per basket)

ATR is the last CLOSED bar's, so a level is fixed before the price reaches it.
Whether an add actually happens is then up to the risk manager.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import GridCfg, GridMode, SizingMode


@dataclass
class AddLevel:
    price: float          # mid price that triggers the add
    distance: float
    adverse: bool         # True = averaging (against the basket), False = pyramiding


def grid_distance(cfg: GridCfg, atr: float | None) -> float | None:
    if cfg.mode == GridMode.NONE:
        return None
    if cfg.mode == GridMode.FIXED:
        return cfg.distance
    if atr is None or not atr > 0:
        return None
    return atr * cfg.atr_multiplier


def next_add_level(direction: str, last_ref_price: float, cfg: GridCfg, sizing: SizingMode,
                   atr: float | None) -> AddLevel | None:
    d = grid_distance(cfg, atr)
    if d is None:
        return None
    adverse = sizing != SizingMode.PYRAMID
    sign = 1 if direction == "BUY" else -1
    price = last_ref_price - sign * d if adverse else last_ref_price + sign * d
    return AddLevel(price, d, adverse)
