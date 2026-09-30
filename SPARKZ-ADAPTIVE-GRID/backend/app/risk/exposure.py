"""Exposure and margin of a basket, in USD."""

from __future__ import annotations

from app.data.instruments import Instrument


def notional(lots: float, price: float, inst: Instrument) -> float:
    return inst.notional_usd(lots, price)


def margin(lots: float, price: float, inst: Instrument) -> float:
    return inst.margin_usd(lots, price)


def leverage(lots: float, price: float, equity: float, inst: Instrument) -> float:
    """Effective leverage: notional / equity. Reported for every basket so no leverage stays hidden."""
    return float("inf") if equity <= 0 else notional(lots, price, inst) / equity


def margin_usage_pct(lots: float, price: float, equity: float, inst: Instrument) -> float:
    return float("inf") if equity <= 0 else margin(lots, price, inst) / equity * 100
