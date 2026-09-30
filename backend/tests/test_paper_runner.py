"""Tests for the run-once, file-backed paper trading runner (app.paper.runner)."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import app.paper.runner as runner
from app.data.validator import closed_candles
from app.paper.runner import PaperRunConfig, init_state, load_state, run_step, save_state
from app.paper.simulator import PaperAccountState, PaperTradingSimulator
from app.strategy.rules import rule_signal


def _daily(n=300, start="2025-01-01"):
    rng = np.random.default_rng(7)
    close = 60_000 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n)))  # BTC-like level: real BTC costs apply
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=n, freq="1D", tz="UTC"),
        "open": open_, "high": np.maximum(open_, close) * 1.01, "low": np.minimum(open_, close) * 0.99,
        "close": close, "volume": 1000.0,
    })


def _state():
    return init_state(PaperRunConfig("t", "BTC-USD", "1d", "baseline_long_only", 10_000))


def _force_signal(monkeypatch, value):
    monkeypatch.setattr(runner, "rule_signal", lambda df, strategy, cfg=None: pd.Series(value, index=df.index))


def test_closed_candles_drops_the_still_forming_bar():
    df = _daily(5, "2025-01-01")
    out = closed_candles(df, "1d", now=pd.Timestamp("2025-01-05 12:00", tz="UTC"))
    assert out["timestamp"].iloc[-1] == pd.Timestamp("2025-01-04", tz="UTC")  # Jan 5 closes at Jan 6 00:00


def test_long_only_never_sells():
    from app.features.feature_engineering import build_feature_matrix

    f = build_feature_matrix(_daily())
    both, long_only = rule_signal(f, "baseline"), rule_signal(f, "baseline_long_only")
    assert (both == "SELL").any()
    assert not (long_only == "SELL").any()
    assert ((both == "BUY") == (long_only == "BUY")).all()


def test_daily_loss_resets_on_a_new_day():
    """Regression: the simulator never reset daily_loss, so after a few losing
    days max_daily_loss_pct blocked every new paper trade forever."""
    sim = PaperTradingSimulator()
    acct = PaperAccountState(balance=10_000)
    sim.start(acct)
    acct.risk_state.daily_loss = -1_000  # far beyond the 3% daily limit
    acct.risk_state.current_day = datetime(2025, 1, 1).date()
    pos = sim.open_position(acct, "BTC-USD", "BUY", 100.0, 2.0, timestamp=datetime(2025, 1, 2, tzinfo=timezone.utc))
    assert pos is not None


def test_first_run_opens_on_latest_closed_candle(monkeypatch):
    _force_signal(monkeypatch, "BUY")
    s = _state()
    res = run_step(s, candles=_daily(), now=pd.Timestamp("2026-01-01", tz="UTC"))
    assert "BTC-USD" in s.account.open_positions
    assert res.latest_candle == s.last_processed


def test_same_candle_is_not_processed_twice(monkeypatch):
    _force_signal(monkeypatch, "BUY")
    s, data, now = _state(), _daily(), pd.Timestamp("2026-01-01", tz="UTC")
    run_step(s, candles=data, now=now)
    s.account.open_positions.clear()  # even if flat again...
    res = run_step(s, candles=data, now=now)
    assert res.new_candles == 0 and s.account.open_positions == {}  # ...no re-entry on an old candle


def test_missed_candles_get_stop_checks_but_no_backfilled_entries(monkeypatch):
    _force_signal(monkeypatch, "HOLD")
    data = _daily()
    s = _state()
    s.last_processed = data["timestamp"].iloc[-6].to_pydatetime()
    # An open long whose stop sits inside a candle 3 days before the latest.
    crash = data.index[-3]
    data.loc[crash, "low"] = data.loc[crash, "close"] * 0.5
    entry = float(data.close.iloc[-6])
    PaperTradingSimulator().open_position(s.account, "BTC-USD", "BUY", entry, entry * 0.15,  # stop ~30% below
                                          timestamp=s.last_processed)
    res = run_step(s, candles=data, now=pd.Timestamp("2026-01-01", tz="UTC"))
    assert res.new_candles == 5
    assert "BTC-USD" not in s.account.open_positions
    assert s.account.trade_history[-1].reason == "STOP"
    assert s.account.trade_history[-1].closed_at == data["timestamp"].iloc[crash].to_pydatetime()


def test_state_round_trips_through_disk(tmp_path, monkeypatch):
    _force_signal(monkeypatch, "BUY")
    s = _state()
    run_step(s, candles=_daily(), now=pd.Timestamp("2026-01-01", tz="UTC"))
    path = save_state(s, tmp_path / "t.json")
    back = load_state(path)
    assert back.config == s.config
    assert back.last_processed == s.last_processed
    assert back.account.balance == pytest.approx(s.account.balance)
    assert back.account.open_positions.keys() == s.account.open_positions.keys()
    assert back.account.risk_state == s.account.risk_state


def test_rejects_model_strategies():
    with pytest.raises(ValueError):
        init_state(PaperRunConfig("t", "BTC-USD", "1d", "random_forest_x", 10_000))
