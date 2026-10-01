"""
Position sizing per entry number within a basket (entry 1 = the initial position).

  FIXED       base, base, base, base ...
  LINEAR      base, 2 x base, 3 x base, 4 x base ...
  PYRAMID     base each time, but the grid adds only when price moves IN FAVOUR
  MULTIPLIER  base, m x base, m^2 x base ...   HIGH RISK: exposure grows geometrically
              while the basket is losing. Refused unless sizing.allow_multiplier_sizing.

Sizes never depend on whether the previous basket won or lost.
"""

from __future__ import annotations

from app.config import SizingCfg, SizingMode
from app.market.instruments import Instrument

HIGH_RISK_MODES = {SizingMode.MULTIPLIER}


def lots_for_entry(n: int, cfg: SizingCfg, inst: Instrument) -> float:
    if n < 1:
        raise ValueError("entry numbers start at 1")
    b = cfg.base_lot
    if cfg.mode in (SizingMode.FIXED, SizingMode.PYRAMID):
        lots = b
    elif cfg.mode == SizingMode.LINEAR:
        lots = b * n
    elif cfg.mode == SizingMode.MULTIPLIER:
        if not cfg.allow_multiplier_sizing:
            raise PermissionError("MULTIPLIER sizing is HIGH RISK and disabled. Set ALLOW_MULTIPLIER_SIZING to test it.")
        lots = b * cfg.multiplier ** (n - 1)
    else:  # pragma: no cover
        raise ValueError(cfg.mode)
    return inst.round_lot(lots)


def planned_lots(max_positions: int, cfg: SizingCfg, inst: Instrument) -> list[float]:
    return [lots_for_entry(k, cfg, inst) for k in range(1, max_positions + 1)]


def describe(cfg: SizingCfg, max_positions: int, inst: Instrument) -> dict:
    lots = planned_lots(max_positions, cfg, inst)
    return {"mode": cfg.mode.value, "high_risk": cfg.mode in HIGH_RISK_MODES, "planned_lots": lots,
            "max_total_lots": round(sum(lots), 4)}
