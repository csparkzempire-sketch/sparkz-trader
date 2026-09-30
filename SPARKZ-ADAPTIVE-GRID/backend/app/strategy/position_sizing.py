"""
Position sizing per entry number within a basket (entry 1 = the initial trade).

  FIXED       base, base, base, base ...
  LINEAR      base, 2 x base, 3 x base, 4 x base ...
  PYRAMID     base each time, but adds happen only on FAVOURABLE moves (see grid_engine)
  MARTINGALE  base, m x base, m^2 x base ...   HIGH RISK: exposure grows geometrically while
              the basket is losing. Refused unless sizing.allow_martingale is true.

The base lot is either fixed (sizing.base_lot) or, with base_lot_mode
ATR_NORMALIZED, set once per basket at its start so that a 1-ATR move on the
base lot is worth sizing.usd_per_atr. That keeps risk comparable across markets
whose price scales differ by orders of magnitude (0.01 lot of gold moves about
$8 per 15m ATR; 0.01 lot of EURUSD about $0.36).

Sizes never depend on whether the previous basket won or lost: a loss never
makes the next basket bigger.
"""

from __future__ import annotations

from app.config import BaseLotMode, SizingCfg, SizingMode
from app.data.instruments import Instrument

HIGH_RISK_MODES = {SizingMode.MARTINGALE}


def base_lot(cfg: SizingCfg, inst: Instrument, atr: float, price: float) -> float:
    """The basket's base lot, fixed at its start (ATR from the signal bar, so no look-ahead)."""
    if cfg.base_lot_mode == BaseLotMode.FIXED or not atr or atr != atr or atr <= 0:
        return cfg.base_lot
    usd_per_atr_per_lot = inst.usd_per_price_unit(1.0, price) * atr
    return inst.round_lot(cfg.usd_per_atr / usd_per_atr_per_lot)


def lot_for_entry(entry_number: int, cfg: SizingCfg, inst: Instrument, base: float | None = None) -> float:
    if entry_number < 1:
        raise ValueError("entry numbers start at 1")
    b = cfg.base_lot if base is None else base
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


def planned_lots(max_positions: int, cfg: SizingCfg, inst: Instrument, base: float | None = None) -> list[float]:
    return [lot_for_entry(k, cfg, inst, base) for k in range(1, max_positions + 1)]
