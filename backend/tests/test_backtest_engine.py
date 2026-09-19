from __future__ import annotations

import pandas as pd
import pytest

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.metrics import compute_metrics


def _make_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.date_range("2023-01-01", periods=len(df), freq="1h", tz="UTC")
    return df


def test_signal_executes_no_earlier_than_next_bar():
    """
    Bar 2 generates a BUY signal at its close. The engine must NOT enter at
    bar 2's open/close — it must enter at bar 3's open. We construct bar 2's
    open price very favorably and bar 3's open price differently, then check
    the recorded entry_price matches bar 3's open (plus costs), not bar 2's.
    """
    rows = [
        {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1000, "atr": 0.0010, "signal": "HOLD"},
        {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1000, "atr": 0.0010, "signal": "HOLD"},
        {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1010, "atr": 0.0010, "signal": "BUY"},  # signal fires here
        {"open": 1.1050, "high": 1.1060, "low": 1.1040, "close": 1.1055, "atr": 0.0010, "signal": "HOLD"},  # execution bar
        {"open": 1.1200, "high": 1.1250, "low": 1.1190, "close": 1.1220, "atr": 0.0010, "signal": "HOLD"},
        {"open": 1.1220, "high": 1.1230, "low": 1.1210, "close": 1.1225, "atr": 0.0010, "signal": "HOLD"},
    ]
    df = _make_df(rows)

    config = BacktestConfig(
        symbol="TEST", timeframe="1h", initial_capital=10_000, risk_per_trade=0.01,
        stop_atr_multiplier=2.0, take_profit_r=2.0, spread_pips=0.0, slippage_pips=0.0, pip_size=0.0001,
    )
    engine = BacktestEngine(config)
    result = engine.run(df, signal_col="signal")

    assert len(result.portfolio.closed_trades) >= 1
    trade = result.portfolio.closed_trades[0]
    # Entry price must equal bar index 3's open (1.1050), NOT bar 2's open/close.
    assert trade.entry_price == pytest.approx(1.1050)
    assert trade.entry_price != pytest.approx(1.1010)


def test_spread_and_slippage_worsen_entry_price():
    rows = [
        {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1000, "atr": 0.0010, "signal": "HOLD"},
        {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1000, "atr": 0.0010, "signal": "BUY"},
        {"open": 1.1050, "high": 1.1060, "low": 1.1040, "close": 1.1055, "atr": 0.0010, "signal": "HOLD"},
        {"open": 1.1200, "high": 1.1250, "low": 1.1190, "close": 1.1220, "atr": 0.0010, "signal": "HOLD"},
        {"open": 1.1220, "high": 1.1230, "low": 1.1210, "close": 1.1225, "atr": 0.0010, "signal": "HOLD"},
    ]
    df = _make_df(rows)
    config = BacktestConfig(
        symbol="TEST", timeframe="1h", initial_capital=10_000, risk_per_trade=0.01,
        stop_atr_multiplier=2.0, take_profit_r=2.0, spread_pips=2.0, slippage_pips=1.0, pip_size=0.0001,
    )
    engine = BacktestEngine(config)
    result = engine.run(df, signal_col="signal")
    trade = result.portfolio.closed_trades[0]
    # BUY entry should be worse (higher) than the raw open due to costs.
    assert trade.entry_price > 1.1050
    assert trade.entry_price == pytest.approx(1.1050 + 3 * 0.0001)


def test_stop_loss_triggers_before_target_when_both_possible_same_bar():
    """
    Conservative assumption: if both stop and target could be hit in the
    same bar, the engine assumes the stop was hit first.
    """
    rows = [
        {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1000, "atr": 0.0010, "signal": "HOLD"},
        {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1000, "atr": 0.0010, "signal": "BUY"},
        # Execution bar: entry fills at this bar's open (1.1000) -> stop = 1.0980, target = 1.1040.
        # Position is opened during this bar, so its own high/low cannot trigger an exit
        # (that would be look-ahead) — the exit check only applies starting next bar.
        {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1000, "atr": 0.0010, "signal": "HOLD"},
        # Next bar: both stop (1.0980) and target (1.1040) fall within this bar's range.
        {"open": 1.1000, "high": 1.1060, "low": 1.0950, "close": 1.1000, "atr": 0.0010, "signal": "HOLD"},
        {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1000, "atr": 0.0010, "signal": "HOLD"},
    ]
    df = _make_df(rows)
    config = BacktestConfig(
        symbol="TEST", timeframe="1h", initial_capital=10_000, risk_per_trade=0.01,
        stop_atr_multiplier=2.0, take_profit_r=2.0, spread_pips=0.0, slippage_pips=0.0, pip_size=0.0001,
    )
    engine = BacktestEngine(config)
    result = engine.run(df, signal_col="signal")
    trade = result.portfolio.closed_trades[0]
    assert trade.reason == "STOP"


def test_rejects_unsorted_input():
    rows = [
        {"open": 1.10, "high": 1.11, "low": 1.09, "close": 1.10, "atr": 0.001, "signal": "HOLD"},
        {"open": 1.10, "high": 1.11, "low": 1.09, "close": 1.10, "atr": 0.001, "signal": "HOLD"},
    ]
    df = _make_df(rows)
    df = df.iloc[::-1].reset_index(drop=True)  # reverse order
    engine = BacktestEngine(BacktestConfig(symbol="TEST", timeframe="1h"))
    with pytest.raises(ValueError):
        engine.run(df, signal_col="signal")


def test_max_drawdown_metric_matches_manual_calc():
    rows = [
        {"open": 1.10, "high": 1.11, "low": 1.09, "close": 1.10, "atr": 0.001, "signal": "HOLD"},
    ] * 5
    df = _make_df(rows)
    engine = BacktestEngine(BacktestConfig(symbol="TEST", timeframe="1h", initial_capital=10_000))
    result = engine.run(df, signal_col="signal")
    metrics = compute_metrics(result.portfolio, "1h")
    # No trades -> flat equity curve -> 0 drawdown, 0 return
    assert metrics.max_drawdown_pct == pytest.approx(0.0)
    assert metrics.total_trades == 0
