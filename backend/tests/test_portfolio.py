"""Unit tests for the Portfolio class's multi-position support, independent
of the backtest engine (which has its own end-to-end tests)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.backtest.portfolio import OpenPosition, Portfolio

T0 = datetime(2023, 1, 1)


def _position(**overrides) -> OpenPosition:
    defaults = dict(
        direction="BUY",
        entry_time=T0,
        entry_price=1.1000,
        stop_price=1.0950,
        target_price=1.1100,
        size=10_000.0,
        model_probability=None,
    )
    defaults.update(overrides)
    return OpenPosition(**defaults)


def test_new_portfolio_has_no_open_positions():
    p = Portfolio(initial_capital=10_000)
    assert p.open_positions == {}
    assert p.has_open_position is False
    assert p.unrealized_pnl(1.2000) == 0.0
    assert p.equity(1.2000) == 10_000


def test_open_assigns_distinct_ids_and_tracks_multiple_positions():
    p = Portfolio(initial_capital=10_000)
    id_a = p.open(_position(entry_price=1.1000))
    id_b = p.open(_position(entry_price=1.1050, entry_time=T0 + timedelta(hours=1)))

    assert id_a != id_b
    assert len(p.open_positions) == 2
    assert p.has_open_position is True
    assert p.open_positions[id_a].entry_price == 1.1000
    assert p.open_positions[id_b].entry_price == 1.1050


def test_unrealized_pnl_sums_across_all_open_positions():
    p = Portfolio(initial_capital=10_000)
    p.open(_position(direction="BUY", entry_price=1.1000, size=10_000))
    p.open(_position(direction="SELL", entry_price=1.1000, size=10_000))

    # BUY gains, SELL loses the same amount at a higher price -> net zero.
    assert p.unrealized_pnl(1.1010) == pytest.approx(0.0)
    # Both positions long-and-short at the same entry: moving price up
    # helps the BUY by exactly as much as it hurts the SELL.
    assert p.equity(1.1010) == pytest.approx(10_000)


def test_closing_one_position_leaves_others_open():
    p = Portfolio(initial_capital=10_000)
    id_a = p.open(_position(entry_price=1.1000, stop_price=1.0950, target_price=1.1100, size=10_000))
    id_b = p.open(_position(entry_price=1.1050, stop_price=1.1000, target_price=1.1150, size=10_000))

    trade = p.close(id_a, T0 + timedelta(hours=2), 1.1050, reason="TARGET")

    assert trade.position_id == id_a
    assert trade.pnl == pytest.approx((1.1050 - 1.1000) * 10_000)
    assert id_a not in p.open_positions
    assert id_b in p.open_positions
    assert len(p.closed_trades) == 1
    # cash reflects only the closed position's pnl; the still-open one
    # hasn't been realized yet.
    assert p.cash == pytest.approx(10_000 + trade.pnl)


def test_close_unknown_position_id_raises():
    p = Portfolio(initial_capital=10_000)
    with pytest.raises(RuntimeError):
        p.close(999, T0, 1.1000, reason="STOP")


def test_close_same_position_twice_raises():
    p = Portfolio(initial_capital=10_000)
    id_a = p.open(_position())
    p.close(id_a, T0 + timedelta(hours=1), 1.1050, reason="TARGET")
    with pytest.raises(RuntimeError):
        p.close(id_a, T0 + timedelta(hours=2), 1.1060, reason="TARGET")
