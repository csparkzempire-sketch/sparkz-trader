"""Trade history across restarts, structure alerts on watched markets, and the assistant backtest job."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.assistant.structure_alerts import StructureAlerts, alert_timeframes
from app.config import load_settings
from app.database.repository import Repository
from app.engine.event_log import LogEntry


def _basket(uid, pnl, day, start=1000.0):
    t = datetime(2026, 11, day, tzinfo=timezone.utc)
    return {"uid": uid, "direction": "BUY", "opened_at": t.isoformat(), "closed_at": (t + timedelta(hours=5)).isoformat(),
            "positions": 1, "pnl": pnl, "mae": 0.0, "close_reason": "TARGET" if pnl > 0 else "LOSS_LIMIT",
            "total_lots": 0.01, "avg_entry": 2650.0, "exit_price": 2650 + pnl, "start_equity": start, "duration_minutes": 300}


class FakeRobot:
    def __init__(self):
        from app.engine.event_log import EventLog
        self.log = EventLog()


def test_trade_history_spans_restarts_and_modes(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path / 't.db'}")
    demo = load_settings(env={"MARKET_DATA_PROVIDER": "BRIDGE", "EXECUTION_MODE": "MT5_DEMO"})
    sim = load_settings(env={})
    for settings, baskets, sync in ((demo, [("A", 50.0, 1), ("B", -30.0, 2)], 1234.5), (sim, [("S", 99.0, 3)], None),
                                    (demo, [("C", 20.0, 4)], 1250.0)):
        run = repo.start_run("PAPER", settings, "test")
        r = FakeRobot()
        repo.attach(r, run)
        if sync:
            r.log.add(datetime(2026, 11, 1, tzinfo=timezone.utc), "ACCOUNT_SYNCED", "synced", {"balance": sync, "currency": "USD"})
        for uid, pnl, day in baskets:
            r.log.add(datetime(2026, 11, day, tzinfo=timezone.utc), "BASKET_CLOSED", "closed", {"basket": _basket(uid, pnl, day)})
    h = repo.trade_history(demo.market.symbol, "MT5_DEMO")
    assert h["runs"] == 2 and [b["uid"] for b in h["baskets"]] == ["A", "B", "C"]
    assert h["starting_balance"] == 1234.5 and h["currency"] == "USD"          # the FIRST sync, not the later one
    s = repo.trade_history(sim.market.symbol, "SIMULATION")
    assert [b["uid"] for b in s["baskets"]] == ["S"] and s["starting_balance"] == 1000.0   # start_equity of its first trade
    assert repo.trade_history(sim.market.symbol, "MT5_LIVE")["baskets"] == []


def _frame(events_at_end: str, n=200):
    t = pd.date_range("2026-11-01", periods=n, freq="1h", tz="UTC")
    c = 2600 + np.sin(np.arange(n) / 6) * 5
    if events_at_end == "up":
        c[-1] = c[:-1].max() + 10                                # close far above every swing high
    df = pd.DataFrame({"timestamp": t, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1.0})
    return df


def test_structure_alert_once_for_a_fresh_break_never_for_history():
    df = _frame("up")
    closed = df["timestamp"].iloc[-1] + pd.Timedelta(hours=1)
    sent = []
    al = StructureAlerts(("1h",), now=lambda: closed.to_pydatetime() + timedelta(minutes=1))
    assert al.check("XAUUSD", lambda tf: df.iloc[:-1], sent.append) == []      # first look: history only
    out = al.check("XAUUSD", lambda tf: df, sent.append)
    assert len(out) == 1 and out[0]["event"].endswith("_UP") and sent == out
    assert al.check("XAUUSD", lambda tf: df, sent.append) == []                # same candle: no repeat
    late = StructureAlerts(("1h",), now=lambda: closed.to_pydatetime() + timedelta(hours=5))
    late.check("XAUUSD", lambda tf: df.iloc[:-1], sent.append)
    assert late.check("XAUUSD", lambda tf: df, sent.append) == []              # too old to be news


def test_alert_timeframes_setting():
    assert alert_timeframes({}) == ("1h", "4h", "1d")
    assert alert_timeframes({"CHART_ALERT_TFS": "off"}) == ()
    assert alert_timeframes({"CHART_ALERT_TFS": "15m, 4h, 2h"}) == ("15m", "4h")


def test_assistant_backtest_job_on_stored_or_given_history(monkeypatch):
    from app.api import backtest as bt
    from app.market.providers.mock_provider import generate_candles
    h1 = generate_candles("XAUUSD", "1h", 24 * 600, "normal", seed=4)
    monkeypatch.setattr(bt, "gold_1h", lambda start, download, progress: h1)
    seen = []
    out = bt._assistant(bt.AssistantBody(execution_tf="4h", start="2000-01-01"), progress=seen.append)
    assert out["meta"]["execution_tf"] == "4h" and out["meta"]["max_risk"] == 400.0 and seen
    if out["overall"]["trades"]:
        assert len(out["trades"]) <= 200 and out["trades"][0]["side"] in ("BUY", "SELL")
        assert len(out["equity_curve"]) == out["overall"]["trades"]
