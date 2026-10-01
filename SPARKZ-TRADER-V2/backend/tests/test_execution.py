"""Simulated execution: spread, slippage, commission, delay."""

import pytest

from tests.conftest import Driver


def entry(**ov):
    d = Driver(**ov)
    d.bar(2000)
    d.tick(2000, gap=True)
    return d


def test_spread_multiplier_and_override():
    assert entry(execution={"spread_multiplier": 3}).basket.positions[0].entry_price == pytest.approx(2000 + 0.45 + 0.05)
    assert entry(execution={"spread_override": 1.0}).basket.positions[0].entry_price == pytest.approx(2000 + 0.5 + 0.05)


def test_slippage_settings():
    assert entry(execution={"slippage": 0.2}).basket.positions[0].entry_price == pytest.approx(2000.35)
    assert entry(execution={"slippage_multiplier": 0}).basket.positions[0].entry_price == pytest.approx(2000.15)


def test_commission_reduces_pnl():
    d = entry(execution={"commission_per_lot_side": 7.0})
    assert d.basket.positions[0].commission == pytest.approx(0.07)
    d.path(2030.0)
    rec = d.robot.completed[-1]
    assert rec.pnl == pytest.approx(10.0, abs=1e-6)     # target is net of commissions


def test_round_trip_cost_without_movement_is_a_loss():
    d = entry()
    d.robot.close_basket_manually()
    assert d.robot.completed[-1].pnl == pytest.approx(-(0.30 + 0.10) * 1, abs=1e-6)


def test_delay_fills_at_the_later_price_not_the_trigger():
    d = Driver(execution={"delay_ms": 5000})
    d.bar(2000)
    d.tick(2000, gap=True)
    assert d.basket is None                       # not due yet
    d.tick(2003.0, seconds=6)
    p = d.basket.positions[0]
    assert p.ref_price == pytest.approx(2003.0)   # latency costs what the market moved
