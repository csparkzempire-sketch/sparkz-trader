"""
Execution model for the backtester.

CRITICAL RULE (see spec section 15): a signal generated using candle N's
close must execute no earlier than candle N+1's open. The backtest engine
enforces this by construction (it looks up `signal.shift(1)` before
evaluating entries) — this module only applies costs to a given raw price,
it does not decide *when* execution happens.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionCosts:
    spread_pips: float
    slippage_pips: float
    commission_per_trade: float
    pip_size: float

    @property
    def total_pips(self) -> float:
        return self.spread_pips + self.slippage_pips

    @property
    def price_adjustment(self) -> float:
        """Total adverse price adjustment in price units (not pips)."""
        return self.total_pips * self.pip_size


def apply_entry_costs(raw_price: float, direction: str, costs: ExecutionCosts) -> float:
    """
    Buying costs you the spread+slippage (you pay a worse price than the
    raw quote); selling similarly gets you a worse price in the other
    direction.
    """
    adj = costs.price_adjustment
    if direction == "BUY":
        return raw_price + adj
    elif direction == "SELL":
        return raw_price - adj
    raise ValueError(f"direction must be 'BUY' or 'SELL', got {direction!r}")


def apply_exit_costs(raw_price: float, direction: str, costs: ExecutionCosts) -> float:
    """Exiting a position means doing the opposite trade, so costs apply in reverse."""
    adj = costs.price_adjustment
    if direction == "BUY":
        # Closing a BUY = selling -> worse (lower) fill price
        return raw_price - adj
    elif direction == "SELL":
        # Closing a SELL = buying -> worse (higher) fill price
        return raw_price + adj
    raise ValueError(f"direction must be 'BUY' or 'SELL', got {direction!r}")
