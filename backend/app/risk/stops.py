"""ATR-based stop-loss and take-profit calculations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StopTargetResult:
    stop_price: float
    target_price: float
    risk_per_unit: float
    reward_per_unit: float


def calculate_stop_and_target(
    entry_price: float,
    atr_value: float,
    direction: str,
    stop_atr_multiplier: float = 2.0,
    take_profit_r: float = 2.0,
) -> StopTargetResult:
    """
    direction: "BUY" or "SELL".
    stop_distance = atr_value * stop_atr_multiplier
    target_distance = stop_distance * take_profit_r  (i.e. take_profit_r "R")
    """
    if direction not in ("BUY", "SELL"):
        raise ValueError(f"direction must be 'BUY' or 'SELL', got {direction!r}")
    if atr_value <= 0:
        raise ValueError("atr_value must be positive")
    if stop_atr_multiplier <= 0:
        raise ValueError("stop_atr_multiplier must be positive")
    if take_profit_r <= 0:
        raise ValueError("take_profit_r must be positive")

    stop_distance = atr_value * stop_atr_multiplier
    target_distance = stop_distance * take_profit_r

    if direction == "BUY":
        stop_price = entry_price - stop_distance
        target_price = entry_price + target_distance
    else:  # SELL
        stop_price = entry_price + stop_distance
        target_price = entry_price - target_distance

    return StopTargetResult(
        stop_price=stop_price,
        target_price=target_price,
        risk_per_unit=stop_distance,
        reward_per_unit=target_distance,
    )
