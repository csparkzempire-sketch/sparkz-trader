"""Margin: used margin, free margin, margin level and usage."""

from __future__ import annotations

from app.market.instruments import Instrument


def used_margin(lots: float, price: float, inst: Instrument) -> float:
    return inst.margin_usd(lots, price)


def margin_usage_pct(lots: float, price: float, equity: float, inst: Instrument) -> float:
    return used_margin(lots, price, inst) / equity * 100 if equity > 0 else float("inf")


def margin_level_pct(equity: float, used: float) -> float | None:
    """Equity / used margin x 100 (brokers typically stop out around 50%). None with nothing open."""
    return equity / used * 100 if used > 0 else None
