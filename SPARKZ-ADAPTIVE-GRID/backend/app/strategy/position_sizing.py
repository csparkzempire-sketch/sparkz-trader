"""
Position sizing per entry number within a basket (entry 1 = the initial trade).

  FIXED       base, base, base, base ...
  LINEAR      base, 2 x base, 3 x base, 4 x base ...
  PYRAMID     base each time, but adds happen only on FAVOURABLE moves (see grid_engine)
  MARTINGALE  base, m x base, m^2 x base ...   HIGH RISK: exposure grows geometrically while
              the basket is losing. Refused unless sizing.allow_martingale is true.

Sizes never depend on whether the previous basket won or lost: a loss never
makes the next basket bigger.
"""

from __future__ import annotations

from app.config import SizingCfg, SizingMode
from app.data.instruments import Instrument

HIGH_RISK_MODES = {SizingMode.MARTINGALE}


def lot_for_entry(entry_number: int, cfg: SizingCfg, inst: Instrument) -> float:
    if entry_number < 1:
        raise ValueError("entry numbers start at 1")
    b = cfg.base_lot
    if cfg.mode == SizingMode.FIXED or cfg.mode == SizingMode.PYRAMID:
        lots = b
    elif cfg.mode == SizingMode.LINEAR:
        lots = b * entry_number
    elif cfg.mode == SizingMode.MARTINGALE:
        if not cfg.allow_martingale:
            raise PermissionError("MARTINGALE sizing is disabled (HIGH RISK). Set allow_martingale to test it.")
        lots = b * cfg.martingale_multiplier ** (entry_number - 1)
    else:  # pragma: no cover - enum is exhaustive
        raise ValueError(cfg.mode)
    return inst.round_lot(lots)


def planned_lots(max_positions: int, cfg: SizingCfg, inst: Instrument) -> list[float]:
    return [lot_for_entry(k, cfg, inst) for k in range(1, max_positions + 1)]
