"""Grid, basket, target, loss limit, cooldown and reset, through the real Robot pipeline."""

import pytest

from tests.conftest import Driver


def opened(**ov):
    d = Driver(**ov)
    d.bar(2000)
    d.tick(2000, gap=True)
    assert d.basket is not None and d.basket.n == 1
    return d


def test_open_fill_includes_spread_and_slippage():
    d = opened()
    p = d.basket.positions[0]
    assert p.ref_price == 2000
    assert p.entry_price == pytest.approx(2000 + 0.15 + 0.05)     # ask + XAUUSD default slippage
    assert d.robot.status == "MANAGING_BASKET"


def test_atr_grid_adds_at_the_level_against_the_basket():
    d = opened()                       # ATR 4 x 0.5 -> 2.0 between entries
    d.path(1999.0, 1997.0)             # crosses 1998 continuously
    b = d.basket
    assert b.n == 2
    assert b.positions[1].ref_price == pytest.approx(1998.0)
    assert b.positions[1].entry_price == pytest.approx(1998.2)
    assert b.avg_entry == pytest.approx((2000.2 + 1998.2) / 2)
    assert "GRID_CHECK" in d.types() and "POSITION_ADDED" in d.types()


def test_fixed_grid_distance():
    d = opened(grid={"mode": "FIXED", "distance": 5.0})
    d.path(1996.0)
    assert d.basket.n == 1
    d.path(1994.0)
    assert d.basket.n == 2 and d.basket.positions[1].ref_price == pytest.approx(1995.0)


def test_one_fast_move_adds_every_crossed_level_then_stops_at_max_positions():
    d = opened()
    d.path(1985.0)                     # 7.5 grid steps in one tick
    b = d.basket
    assert b.n == 5                    # max_positions
    assert [round(p.ref_price, 2) for p in b.positions] == [2000, 1998, 1996, 1994, 1992]
    assert b.adds_blocked and "POSITION_LIMIT" in b.adds_blocked
    d.path(1975.0)
    assert d.basket.n == 5


def test_linear_sizing_lots():
    d = opened(sizing={"mode": "LINEAR"})
    d.path(1993.0)
    assert [p.lots for p in d.basket.positions] == [0.01, 0.02, 0.03, 0.04]


def test_target_closes_whole_basket_at_target_profit_then_resets():
    d = opened()
    d.path(1997.0)                     # 2 positions
    b = d.basket
    tgt_mid = d.robot.strategy.fe.mid_for_close_price("BUY", b.target_exit_price(d.robot.inst, 2000, 0), 0.3)
    d.path(tgt_mid + 3)
    assert d.basket is None
    rec = d.robot.completed[-1]
    assert rec.close_reason == "TARGET" and rec.positions == 2
    assert rec.pnl == pytest.approx(10.0, abs=1e-6)
    assert d.robot.account.balance == pytest.approx(10_010.0)
    assert {"TARGET_REACHED", "BASKET_CLOSED", "RESET", "ACCOUNT_UPDATED"} <= set(d.types())


def test_loss_limit_closes_at_limit_on_continuous_move():
    d = opened(risk={"max_basket_loss_percent": 0.5})   # 50 USD
    for k in range(1, 40):
        d.tick(2000 - k)
        if d.basket is None:
            break
    rec = d.robot.completed[-1]
    assert rec.close_reason == "LOSS_LIMIT"
    assert rec.pnl == pytest.approx(-50.0, abs=1e-6)


def test_gap_through_loss_limit_realizes_the_worse_price():
    d = opened(risk={"max_basket_loss_percent": 0.5})
    d.tick(1900, gap=True)             # far beyond the limit, no prices in between
    rec = d.robot.completed[-1]
    assert rec.close_reason == "LOSS_LIMIT"
    assert rec.pnl < -50.0             # the gap is not hidden


def test_no_immediate_reopen_after_a_win():
    d = opened()
    d.path(2015.0)                     # target
    assert d.basket is None
    bars_blocked = 0
    for _ in range(5):
        d.bar(2015)
        d.tick(2015, gap=True)
        if d.basket is not None:
            break
        bars_blocked += 1
    assert bars_blocked == 2           # the bar it closed in + cooldown_bars_after_close (1)
    assert d.basket is not None


def test_longer_cooldown_after_a_loss():
    d = opened(risk={"max_basket_loss_percent": 0.5, "cooldown_bars_after_loss": 4})
    d.tick(1900, gap=True)
    blocked = 0
    for _ in range(10):
        d.bar(1900)
        d.tick(1900, gap=True)
        if d.basket is not None:
            break
        blocked += 1
    assert blocked == 2 + 4
    assert d.robot.status in ("MANAGING_BASKET",)


def test_no_signal_no_basket():
    d = Driver()
    d.bar(2000, direction=None)
    d.tick(2000, gap=True)
    assert d.basket is None
    assert d.robot.strategy.last_decision.action in ("NO_SIGNAL", "WAIT")


def test_volatility_filter_blocks_new_baskets():
    d = Driver()
    d.bar(2000, vol_regime="HIGH_VOLATILITY", regime="HIGH_VOLATILITY")
    d.tick(2000, gap=True)
    assert d.basket is None


def test_signal_confirmed_grid_stops_adding_when_thesis_breaks():
    d = Driver(grid={"mode": "SIGNAL_CONFIRMED", "atr_multiplier": 0.5})
    d.bar(2000)
    d.tick(2000, gap=True)
    d.bar(1999.5, close=1990.0)        # closed below EMA50 (1994.5): thesis broken
    assert d.basket.adds_blocked and "thesis" in d.basket.adds_blocked
    d.path(1990.0)
    assert d.basket.n == 1
    assert "GRID_STOPPED" in d.types()


def test_pyramid_adds_in_favour_only():
    d = opened(sizing={"mode": "PYRAMID"})
    d.path(1995.0)
    assert d.basket.n == 1
    d.path(2001.0, 2002.5)
    assert d.basket.n == 2 and d.basket.positions[1].ref_price == pytest.approx(2002.0)


def test_basket_drawdown_limit_stops_adds_but_keeps_basket():
    d = opened(risk={"max_basket_drawdown_usd": 3.0})
    d.path(1997.0)                     # add at 1998, floating loss ~ 3.x
    d.path(1995.5)
    b = d.basket
    assert b is not None and b.adds_blocked and "BASKET_DRAWDOWN" in b.adds_blocked


def test_recovery_analysis_is_informational_only():
    d = opened()
    d.path(1985.0)
    snap = d.robot.snapshot()
    rec = snap["basket"]["recovery"]
    assert rec["break_even"]["move"] > 0
    assert d.basket.n == 5             # nothing was added by the analysis
    x = rec["extra_lots_for_be_within_1atr"]
    assert x is None or x["note"].startswith("analysis only")
