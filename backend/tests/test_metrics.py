"""Tests for timeframe-aware duration metrics in app.backtest.metrics."""

from __future__ import annotations

import pandas as pd
import pytest

from app.backtest.metrics import compute_metrics
from app.backtest.portfolio import ClosedTrade, Portfolio


def _portfolio(freq: str, bars: int, hold_bars: int) -> Portfolio:
    """`bars` equity points at `freq`, with one trade held for `hold_bars` bars."""
    ts = pd.date_range("2025-01-01", periods=bars, freq=freq, tz="UTC")
    p = Portfolio(initial_capital=10_000)
    p.equity_curve = [(t, 10_000.0) for t in ts]
    p.closed_trades = [ClosedTrade(
        direction="BUY", entry_time=ts[0], exit_time=ts[hold_bars],
        entry_price=1.0, exit_price=1.0, stop_price=0.9, target_price=1.2, size=1.0, pnl=0.0, reason="TARGET",
    )]
    return p


@pytest.mark.parametrize("timeframe,freq", [("1h", "1h"), ("4h", "4h"), ("1d", "1D")])
def test_holding_time_and_exposure_are_counted_in_the_backtests_own_bars(timeframe, freq):
    """Regression: both were computed in hours whatever the timeframe, so a
    daily trade held 5 bars was reported as 120 'bars' and exposure capped at 100%."""
    m = compute_metrics(_portfolio(freq, bars=20, hold_bars=5), timeframe)
    assert m.average_holding_bars == pytest.approx(5)
    assert m.exposure_pct == pytest.approx(25.0)  # 5 of 20 bars
