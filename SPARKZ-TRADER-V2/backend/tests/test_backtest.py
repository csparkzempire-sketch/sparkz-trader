"""Backtest engine: no look-ahead, consistent accounting, the same robot as paper trading."""

import pandas as pd
import pytest

from app.backtest.engine import bar_ticks, intrabar_path, run_backtest
from app.backtest.metrics import report
from app.config import load_settings
from app.market.indicators import FEATURE_COLUMNS
from app.market.market_engine import featurize
from app.market.providers.historical_provider import HistoricalDataProvider
from app.market.providers.mock_provider import generate_candles


@pytest.fixture(scope="module")
def candles():
    return generate_candles("XAUUSD", "15m", 1500, "reversals", seed=11)


@pytest.fixture(scope="module")
def result(candles):
    return run_backtest(load_settings(env={}), candles, "test")


def test_features_do_not_look_ahead(candles):
    s = load_settings(env={})
    full = featurize(candles, s)
    cut = 900
    part = featurize(candles.iloc[:cut].copy(), s)
    cols = [c for c in FEATURE_COLUMNS if c in part.columns] + ["regime", "trend_regime", "vol_regime"]
    pd.testing.assert_frame_equal(full.iloc[:cut][cols].reset_index(drop=True), part[cols].reset_index(drop=True),
                                  check_exact=False, rtol=1e-9, atol=1e-9)


def test_historical_provider_refuses_future_bars(candles):
    p = HistoricalDataProvider(candles)
    p.cursor = 10
    assert len(p.get_candles("15m", 100)) == 11
    with pytest.raises(IndexError):
        p.bar(11)


def test_accounting_is_consistent(result):
    r = result.robot
    total = sum(b.pnl for b in r.completed)
    assert r.strategy.basket is None                         # END_OF_DATA closed anything open
    assert r.account.balance == pytest.approx(10_000 + total, abs=1e-6)
    assert r.account.equity == pytest.approx(r.account.balance)
    opens = sum(1 for f in r.ledger.fills if f.type.value == "OPEN_BASKET")
    assert opens == len(r.completed)
    for b in r.completed:
        assert b.mae <= min(b.pnl, 0) + 1e-9 and b.mfe >= max(b.pnl, 0) - 1e-9 or b.close_reason != "TARGET"
        assert 1 <= b.positions <= 5


def test_entries_only_after_warmup(result):
    first = result.features.index[result.features["regime"] != "WARMUP"][0]
    first_time = result.candles["timestamp"].iloc[first]
    assert all(b.opened_at > first_time for b in result.robot.completed)


def test_baskets_never_overlap(result):
    bs = result.robot.completed
    for a, b in zip(bs, bs[1:]):
        assert b.opened_at >= a.closed_at


def test_report_shows_losses_and_assumptions(result):
    rep = report(result)
    m = rep["metrics"]
    assert rep["simulated"] is True and rep["assumptions"]
    for k in ("largest_basket_loss", "max_drawdown_pct", "worst_floating_pnl", "mae_mfe", "by_regime", "costs",
              "buy_and_hold", "max_consecutive_losses", "max_margin_usage_pct", "max_effective_leverage"):
        assert k in m
    assert m["baskets"] == len(result.robot.completed)


def test_intrabar_path_orders():
    assert intrabar_path(10, 12, 9, 11, None) == [10, 9, 12, 11]        # up candle: low first
    assert intrabar_path(10, 12, 9, 9.5, None) == [10, 12, 9, 9.5]      # down candle: high first
    assert intrabar_path(10, 12, 9, 11, False) == [10, 12, 9, 11]
    ticks = bar_ticks("XAUUSD", pd.Timestamp("2025-01-01", tz="UTC").to_pydatetime(), 900, 10, 12, 9, 11, 0.3, None)
    assert ticks[0].gap and not any(t.gap for t in ticks[1:])
    assert (ticks[-1].time - ticks[0].time).total_seconds() == 899


def test_intrabar_order_is_a_reported_sensitivity(candles):
    fav = run_backtest(load_settings(env={}), candles)
    adv = run_backtest(load_settings(env={}, overrides={"execution": {"intrabar_order": "ADVERSE_FIRST"}}), candles)
    assert sum(b.pnl for b in fav.robot.completed) != sum(b.pnl for b in adv.robot.completed)


def test_backtest_is_deterministic(candles, result):
    again = run_backtest(load_settings(env={}), candles)
    assert [b.pnl for b in again.robot.completed] == [b.pnl for b in result.robot.completed]
