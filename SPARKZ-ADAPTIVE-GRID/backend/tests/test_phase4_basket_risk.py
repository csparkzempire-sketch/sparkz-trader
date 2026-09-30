"""Phases 4-6: sizing, grid spacing, basket math, execution costs and the risk engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import (HARD_MAX_POSITIONS, ExecutionCfg, GridCfg, GridMode, SizingCfg, SizingMode, TargetMode,
                        load_config)
from app.backtest.execution import ExecutionModel
from app.data.instruments import get_instrument
from app.risk.drawdown import DrawdownTracker
from app.risk.risk_manager import RiskManager
from app.strategy.basket_manager import Basket, Position, basket_limits
from app.strategy.grid_engine import grid_distance, next_add_level
from app.strategy.position_sizing import lot_for_entry, planned_lots

GOLD = get_instrument("XAUUSD")
T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


# --- sizing ---------------------------------------------------------------------------------------

def test_sizing_modes():
    assert planned_lots(4, SizingCfg(mode=SizingMode.FIXED), GOLD) == [0.01] * 4
    assert planned_lots(4, SizingCfg(mode=SizingMode.LINEAR), GOLD) == pytest.approx([0.01, 0.02, 0.03, 0.04])
    assert planned_lots(4, SizingCfg(mode=SizingMode.PYRAMID), GOLD) == [0.01] * 4
    m = SizingCfg(mode=SizingMode.MARTINGALE, allow_martingale=True)
    assert planned_lots(4, m, GOLD) == pytest.approx([0.01, 0.02, 0.04, 0.08])


def test_martingale_is_refused_without_permission():
    with pytest.raises(PermissionError, match="HIGH RISK"):
        lot_for_entry(2, SizingCfg(mode=SizingMode.MARTINGALE), GOLD)


def test_lots_are_rounded_to_the_lot_step():
    assert lot_for_entry(1, SizingCfg(base_lot=0.013), GOLD) == pytest.approx(0.01)
    assert lot_for_entry(1, SizingCfg(base_lot=0.001), GOLD) == pytest.approx(0.01)  # never below the minimum


# --- grid -----------------------------------------------------------------------------------------

def test_price_grid_with_growing_steps():
    g = GridCfg(mode=GridMode.PRICE, price_step=1.0, step_growth=1.0)
    assert [grid_distance(n, 0, g) for n in (1, 2, 3)] == [1.0, 2.0, 3.0]


def test_atr_grid_distance():
    assert grid_distance(3, 2.5, GridCfg(mode=GridMode.ATR, atr_multiplier=0.5)) == pytest.approx(1.25)
    assert grid_distance(1, float("nan"), GridCfg(mode=GridMode.ATR)) is None
    assert grid_distance(1, 2.5, GridCfg(mode=GridMode.NONE)) is None


def test_averaging_adds_against_the_basket_and_pyramiding_with_it():
    g = GridCfg(mode=GridMode.PRICE, price_step=1.0)
    sell = next_add_level("SELL", 2350.0, 1, 0, g, SizingMode.FIXED)
    assert sell.price == pytest.approx(2351.0) and sell.adverse
    buy = next_add_level("BUY", 2350.0, 1, 0, g, SizingMode.FIXED)
    assert buy.price == pytest.approx(2349.0)
    pyr = next_add_level("SELL", 2350.0, 1, 0, g, SizingMode.PYRAMID)
    assert pyr.price == pytest.approx(2349.0) and not pyr.adverse


# --- basket math ----------------------------------------------------------------------------------

def _basket(direction="SELL", entries=((0.01, 2350.0), (0.02, 2352.0)), commission=0.0):
    b = Basket("BASKET-000001", direction, T0, 0, 10_000, 100.0, 50.0, None, 2.0, "TRENDING_DOWN", "NORMAL_VOLATILITY")
    for i, (lots, px) in enumerate(entries, 1):
        b.positions.append(Position(i, direction, lots, T0 + timedelta(minutes=15 * i), px, px, commission * lots))
    return b


def test_average_price_is_lot_weighted():
    b = _basket()
    assert b.total_lots == pytest.approx(0.03)
    assert b.avg_entry == pytest.approx((0.01 * 2350 + 0.02 * 2352) / 0.03)


def test_basket_pnl():
    b = _basket()   # SELL 0.01 @ 2350 and 0.02 @ 2352: 3 oz in total
    # Close at 2348: (2350-2348)*1 oz + (2352-2348)*2 oz = 2 + 8 = $10
    assert b.pnl(2348.0, GOLD, 2348.0) == pytest.approx(10.0)
    assert b.pnl(2355.0, GOLD, 2355.0) == pytest.approx(-5.0 - 6.0)
    buy = _basket("BUY", ((0.10, 100.0),))
    assert buy.pnl(101.0, GOLD, 101.0) == pytest.approx(10.0)


def test_commissions_come_off_basket_pnl():
    b = _basket(commission=7.0)            # $7 per lot per side
    assert b.pnl(2348.0, GOLD, 2348.0, exit_commission_per_lot=7.0) == pytest.approx(10.0 - 0.21 - 0.21)


def test_target_and_stop_prices_invert_the_pnl():
    b = _basket()
    for usd in (50.0, -100.0, 0.0):
        p = b.exit_price_for_pnl(usd, GOLD, 2350.0)
        assert b.pnl(p, GOLD, 2350.0) == pytest.approx(usd)
    assert b.target_exit_price(GOLD, 2350.0) < b.avg_entry < b.stop_exit_price(GOLD, 2350.0)   # SELL


def test_atr_target_is_a_price_distance():
    b = _basket()
    b.target_usd, b.target_distance = None, 3.0
    assert b.target_exit_price(GOLD, 2350.0) == pytest.approx(b.avg_entry - 3.0)


def test_usdjpy_basket_pnl_in_usd():
    jpy = get_instrument("USDJPY")
    b = _basket("BUY", ((1.0, 150.00),))
    assert b.pnl(150.15, jpy, 150.15) == pytest.approx(0.15 * 100_000 / 150.15)


def test_basket_limits_by_target_mode():
    s = load_config(env={})
    assert basket_limits(s, 10_000, 5.0) == (pytest.approx(100.0), pytest.approx(50.0), None)   # 1% stop, 0.5% target
    s.target.mode = TargetMode.FIXED
    assert basket_limits(s, 10_000, 5.0)[1] == 10.0
    s.target.mode = TargetMode.RISK_REWARD
    s.target.risk_reward = 1.5
    assert basket_limits(s, 10_000, 5.0)[1] == pytest.approx(150.0)
    s.target.mode = TargetMode.ATR
    assert basket_limits(s, 10_000, 5.0)[1:] == (None, pytest.approx(5.0))
    s.risk.risk_per_cycle = 0.005        # the tighter of the two loss settings wins
    assert basket_limits(s, 10_000, 5.0)[0] == pytest.approx(50.0)


def test_basket_status_block():
    st = _basket().status(2349.0, GOLD, 2349.0)
    assert st["direction"] == "SELL" and st["positions"] == 2 and st["basket_target"] == 50.0
    assert len(st["entries"]) == 2


# --- execution costs --------------------------------------------------------------------------------

def test_fills_pay_half_the_spread_plus_slippage():
    ex = ExecutionModel(GOLD, ExecutionCfg(spread_override=0.30, slippage_override=0.05))
    assert ex.entry_fill("BUY", 2350.0) == pytest.approx(2350.20)
    assert ex.entry_fill("SELL", 2350.0) == pytest.approx(2349.80)
    assert ex.exit_fill("BUY", 2350.0) == pytest.approx(2349.80)
    assert ex.exit_mid_for("BUY", ex.exit_fill("BUY", 2350.0)) == pytest.approx(2350.0)
    # a round trip at an unchanged mid costs the full spread plus two slippages
    b = _basket("BUY", ((0.01, ex.entry_fill("BUY", 2350.0)),))
    assert b.pnl(ex.exit_fill("BUY", 2350.0), GOLD, 2350.0) == pytest.approx(-(0.30 + 0.10) * 1)


def test_bar_spread_and_stress_multiplier():
    ex = ExecutionModel(GOLD, ExecutionCfg(spread_multiplier=3.0))
    assert ex.spread({"spread": 0.20}) == pytest.approx(0.60)
    assert ex.spread({"spread": float("nan")}) == pytest.approx(GOLD.spread * 3)


# --- risk engine -----------------------------------------------------------------------------------

def _rm(**env):
    return RiskManager(load_config(env={k: str(v) for k, v in env.items()}), GOLD)


def test_position_limit_is_hard():
    rm = _rm(MAX_POSITIONS=5)
    assert rm.check_add(4, 0.04, 0.01, 2350, 10_000).allowed
    d = rm.check_add(5, 0.05, 0.01, 2350, 10_000)
    assert not d.allowed and d.code == "POSITION_LIMIT"
    rm.s.risk.max_positions = 999      # even a config mutated after validation can't lift the ceiling
    assert not rm.check_add(HARD_MAX_POSITIONS, 0.2, 0.01, 2350, 1e9).allowed


def test_exposure_and_margin_limits():
    rm = _rm(MAX_EXPOSURE_LEVERAGE=5)
    d = rm.check_add(1, 0.10, 0.20, 2350, 10_000)      # 0.30 lot = 30 oz = $70.5k = 7x
    assert not d.allowed and d.code == "EXPOSURE"
    rm = _rm(MAX_EXPOSURE_LEVERAGE=100, MAX_MARGIN_USAGE_PERCENT=5)
    d = rm.check_add(1, 0.10, 0.20, 2350, 10_000)      # margin $705 = 7% of equity
    assert not d.allowed and d.code == "MARGIN"


def test_daily_loss_blocks_new_positions():
    rm = _rm(MAX_DAILY_LOSS_PERCENT=3)
    rm.dd.update(10_000, T0)
    rm.dd.update(9_690, T0 + timedelta(hours=3))
    d = rm.check_open(0.01, 2350, 9_690, 10)
    assert not d.allowed and d.code == "DAILY_LOSS"
    rm.dd.update(9_690, T0 + timedelta(days=1))          # new UTC day resets the tally
    assert rm.check_open(0.01, 2350, 9_690, 11).allowed


def test_cooldown_after_a_loss_only():
    rm = _rm(COOLDOWN_BARS_AFTER_LOSS=8)
    rm.on_basket_closed(+20.0, 100)
    assert rm.check_open(0.01, 2350, 10_000, 101).allowed
    rm.on_basket_closed(-20.0, 100)
    assert rm.check_open(0.01, 2350, 10_000, 108).code == "COOLDOWN"
    assert rm.check_open(0.01, 2350, 10_000, 109).allowed


def test_account_drawdown_halts_and_fails_closed():
    rm = _rm(MAX_ACCOUNT_DRAWDOWN_PERCENT=10)
    rm.dd.update(11_000)
    assert rm.check_account(10_000) is None
    assert rm.check_account(9_899) == "CLOSE_AND_HALT"
    rm.halt("account drawdown 10%")
    assert rm.check_open(0.01, 2350, 9_899, 500).code == "HALTED"
    assert rm.check_add(1, 0.01, 0.01, 2350, 9_899).code == "HALTED"
    rm.reset_halt()
    assert rm.check_open(0.01, 2350, 9_899, 500).allowed


def test_drawdown_tracker():
    t = DrawdownTracker(peak=10_000)
    t.update(10_500)
    assert t.update(9_450) == pytest.approx(10.0) and t.max_drawdown_pct == pytest.approx(10.0)
    t.update(10_600)
    assert t.max_drawdown_pct == pytest.approx(10.0) and t.current_pct(10_600) == 0


def test_atr_normalized_base_lot_equalises_risk_across_markets():
    from app.config import BaseLotMode
    from app.strategy.position_sizing import base_lot

    cfg = SizingCfg(base_lot_mode=BaseLotMode.ATR_NORMALIZED, usd_per_atr=10.0)
    eur = get_instrument("EURUSD")
    assert base_lot(cfg, GOLD, 8.0, 4200.0) == pytest.approx(0.01)        # $800 per ATR per lot -> 0.0125 -> 0.01
    assert base_lot(cfg, eur, 0.00036, 1.13) == pytest.approx(0.28)         # $36 per ATR per lot -> 0.278
    assert base_lot(SizingCfg(), eur, 0.00036, 1.13) == pytest.approx(0.01)  # FIXED ignores ATR
    assert base_lot(cfg, eur, float("nan"), 1.13) == pytest.approx(0.01)     # no ATR: fall back to the fixed lot
    # grid sizes scale from the basket's base lot
    lin = SizingCfg(mode=SizingMode.LINEAR)
    assert planned_lots(3, lin, eur, 0.28) == pytest.approx([0.28, 0.56, 0.84])
