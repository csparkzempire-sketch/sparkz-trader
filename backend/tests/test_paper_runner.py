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


def _saved_account(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PAPER_DIR", tmp_path)
    _force_signal(monkeypatch, "BUY")
    s = _state()
    run_step(s, candles=_daily(), now=pd.Timestamp("2026-01-01", tz="UTC"))
    save_state(s, tmp_path / "t.json")
    return s


def test_runs_endpoint_marks_open_positions_to_latest_price(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.api.routes_paper as routes
    from app.main import app

    s = _saved_account(tmp_path, monkeypatch)
    pos = s.account.open_positions["BTC-USD"]
    price = pos.entry_price * 1.05
    monkeypatch.setattr(routes, "_latest_price", lambda sym: (price, pd.Timestamp("2026-01-01", tz="UTC"), None))

    [acct] = TestClient(app).get("/paper/runs").json()
    assert acct["account_name"] == "t" and acct["strategy"] == "baseline_long_only"
    [p] = acct["open_positions"]
    assert p["unrealized_pnl"] == pytest.approx((price - pos.entry_price) * pos.size)
    assert acct["equity"] == pytest.approx(10_000 + p["unrealized_pnl"])
    assert p["stop_distance_pct"] < 0 < p["target_distance_pct"]


def test_runs_endpoint_still_works_when_price_is_unavailable(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.api.routes_paper as routes
    from app.main import app

    _saved_account(tmp_path, monkeypatch)
    monkeypatch.setattr(routes, "_latest_price", lambda sym: (None, None, "Yahoo unreachable"))

    [acct] = TestClient(app).get("/paper/runs").json()
    assert acct["price_error"] == "Yahoo unreachable"
    assert acct["equity"] == pytest.approx(acct["balance"])
    assert acct["open_positions"][0]["unrealized_pnl"] is None


def test_runs_endpoint_is_empty_without_accounts(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(runner, "PAPER_DIR", tmp_path / "none")
    assert TestClient(app).get("/paper/runs").json() == []


# --- Drawdown halt ------------------------------------------------------------

def _halt_setup(monkeypatch):
    """An account with an open BUY whose tight stop the next candle hits, under a
    0.5% drawdown limit, so the ~1% stop-out loss breaches it."""
    from app.config import settings

    cfg = settings.model_copy(update={"max_drawdown_pct": 0.005, "max_daily_loss_pct": 1.0})
    _force_signal(monkeypatch, "BUY")
    data = _daily()
    s = _state()
    s.last_processed = data["timestamp"].iloc[-3].to_pydatetime()
    entry = float(data.close.iloc[-3])
    PaperTradingSimulator(cfg).open_position(s.account, "BTC-USD", "BUY", entry, entry * 0.001,
                                             timestamp=s.last_processed)
    return s, data, cfg


def test_drawdown_breach_halts_the_account_and_says_so_once(monkeypatch):
    s, data, cfg = _halt_setup(monkeypatch)
    now = pd.Timestamp("2026-01-01", tz="UTC")

    res = run_step(s, candles=data.iloc[:-1], cfg=cfg, now=now)
    assert s.account.trade_history[-1].reason == "STOP"
    assert s.halted is not None and "drawdown" in s.halted
    assert sum("HALTED" in e for e in res.events) == 1
    assert "BTC-USD" not in s.account.open_positions  # BUY signal blocked

    res2 = run_step(s, candles=data, cfg=cfg, now=now)  # one more candle, BUY again
    assert res2.new_candles == 1
    assert not any("HALTED" in e or "skipped" in e for e in res2.events)  # no repeat spam
    assert "BTC-USD" not in s.account.open_positions


def test_halt_survives_save_and_load(tmp_path, monkeypatch):
    s, data, cfg = _halt_setup(monkeypatch)
    run_step(s, candles=data.iloc[:-1], cfg=cfg, now=pd.Timestamp("2026-01-01", tz="UTC"))
    back = load_state(save_state(s, tmp_path / "t.json"))
    assert back.halted == s.halted


def test_resume_lifts_the_halt_and_trading_restarts(monkeypatch):
    from app.paper.runner import resume

    s, data, cfg = _halt_setup(monkeypatch)
    now = pd.Timestamp("2026-01-01", tz="UTC")
    run_step(s, candles=data.iloc[:-1], cfg=cfg, now=now)
    assert s.halted

    event = resume(s, now.to_pydatetime())
    assert "RESUMED" in event and s.halted is None
    assert s.account.risk_state.peak_equity == pytest.approx(s.account.balance)

    run_step(s, candles=data, cfg=cfg, now=now)
    assert "BTC-USD" in s.account.open_positions


def test_runs_endpoint_reports_halted_accounts(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.api.routes_paper as routes
    from app.main import app

    monkeypatch.setattr(runner, "PAPER_DIR", tmp_path)
    s, data, cfg = _halt_setup(monkeypatch)
    run_step(s, candles=data.iloc[:-1], cfg=cfg, now=pd.Timestamp("2026-01-01", tz="UTC"))
    save_state(s, tmp_path / "t.json")
    monkeypatch.setattr(routes, "_latest_price", lambda sym: (None, None, "offline"))

    [acct] = TestClient(app).get("/paper/runs").json()
    assert acct["halted"] == s.halted


# --- Snapshot feed for the hosted page -----------------------------------------

def test_snapshot_and_history_feed(tmp_path, monkeypatch):
    import json
    from datetime import timedelta

    from app.paper import snapshot
    from app.paper.summary import summarize

    _saved_account(tmp_path, monkeypatch)
    states = runner.list_states()
    summaries = summarize(states, lambda sym: (60_000.0, pd.Timestamp("2026-01-01", tz="UTC"), None))
    now = datetime(2026, 1, 1, 10, 20, tzinfo=timezone.utc)
    hist = tmp_path / "hist.jsonl"

    snap = snapshot.build_snapshot(summaries, now)
    json.dumps(snap)  # must be plain JSON for the page database
    assert snap["totals"]["accounts"] == 1 and snap["accounts"][0]["evaluation"]["verdict"]

    snapshot.append_history(summaries, now, hist)
    snapshot.append_history(summaries, now + timedelta(minutes=30), hist)  # same hour: replaced
    snapshot.append_history(summaries, now + timedelta(hours=1), hist)
    doc = snapshot.history_doc(hist)
    assert doc["t"] == ["2026-01-01T10:00:00+00:00", "2026-01-01T11:00:00+00:00"]
    assert list(doc["accounts"]) == ["t"] and len(doc["total"]) == 2
