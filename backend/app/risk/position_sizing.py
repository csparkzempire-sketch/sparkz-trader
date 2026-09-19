"""Position sizing based on configured risk-per-trade and stop distance."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PositionSizeResult:
    position_size: float  # units of the asset
    dollar_risk: float
    stop_distance: float


def calculate_position_size(
    account_equity: float,
    risk_per_trade: float,
    entry_price: float,
    stop_price: float,
) -> PositionSizeResult:
    """
    Standard fixed-fractional position sizing:
        dollar_risk = account_equity * risk_per_trade
        position_size = dollar_risk / stop_distance

    This limits risk according to the simulation's assumptions (fixed
    entry, no slippage on the stop itself) — it does NOT make the strategy
    safe in absolute terms. Gaps, slippage on stop execution, and
    correlated positions can all cause realized loss to exceed the target.
    """
    if account_equity <= 0:
        raise ValueError("account_equity must be positive")
    if not (0 < risk_per_trade < 1):
        raise ValueError("risk_per_trade must be a fraction between 0 and 1, e.g. 0.01 for 1%")
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        raise ValueError("stop_distance must be positive (entry_price and stop_price cannot be equal)")

    dollar_risk = account_equity * risk_per_trade
    position_size = dollar_risk / stop_distance

    return PositionSizeResult(position_size=position_size, dollar_risk=dollar_risk, stop_distance=stop_distance)
