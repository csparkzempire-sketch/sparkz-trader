"""Exposure: notional value and leverage of open positions."""

from __future__ import annotations

from app.market.instruments import Instrument


def notional(lots: float, price: float, inst: Instrument) -> float:
    return inst.notional_usd(lots, price)


def leverage(lots: float, price: float, equity: float, inst: Instrument) -> float:
    return notional(lots, price, inst) / equity if equity > 0 else float("inf")
