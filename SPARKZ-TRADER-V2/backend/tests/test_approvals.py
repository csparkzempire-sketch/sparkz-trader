"""Trade approvals: proposals on watched markets, approval checks, the MT5 order, follow-up, Telegram buttons."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.approvals import manager as mgr
from app.approvals.manager import ApprovalConfig, ApprovalError, ApprovalManager
from app.approvals.telegram_bot import TelegramApprovals, proposal_text
from app.execution.mt5_executor import OrderBook
from app.market.watch import WatchStore

NOW = pd.Timestamp("2026-11-20 00:05", tz="UTC")           # just after a daily close


def watch_with(symbol="EURUSDm", price=1.10, clock=None):
    w = WatchStore(clock=clock or (lambda: NOW.timestamp()))
    rows = {}
    for tf, freq, n in (("1h", "1h", 400), ("4h", "4h", 300), ("1d", "1D", 200)):
        t = pd.date_range(end=NOW.floor("1D"), periods=n, freq=freq)     # the last one is forming
        c = price + np.sin(np.arange(n) / 5) * 0.002
        rows[tf] = [{"time": x.isoformat(), "open": v, "high": v + 0.001, "low": v - 0.001, "close": v} for x, v in zip(t, c)]
        rows[tf][-1]["forming"] = True
    w.push(symbol, {"time": NOW.isoformat(), "bid": price, "ask": price + 0.0001}, rows, 5)
    return w


class FakeExecutor:
    def __init__(self, problem=None):
        self.problem = problem

    def account_problem(self):
        return self.problem


def report(action="BUY", entry=1.10, stop=1.09, target=1.12):
    return {"action": action, "confidence_pct": 72.0, "higher_timeframe_bias": "BULLISH", "reasons": ["3 strategies agree"],
            "strategy_fit_pct": {"trend_following": 30.0, "support_resistance": 20.0},
            "plan": {"strategy": "trend_following", "tf": "1d", "entry": entry, "stop": stop, "target": target,
                     "history": {"samples": 40, "hit_rate_pct": 45.0}}}


@pytest.fixture
def setup(monkeypatch):
    t = [NOW.timestamp()]
    book = OrderBook(clock=lambda: t[0])
    w = watch_with(clock=lambda: t[0])
    ex = FakeExecutor()
    told = []
    notify = SimpleNamespace(proposal=lambda p: told.append(("proposal", p["id"])), message=lambda s: told.append(("msg", s)))
    m = ApprovalManager(ApprovalConfig(), book=book, watch=w, executor_fn=lambda: ex, robot_symbol="XAUUSD",
                        clock=lambda: t[0], path=None, notify=notify)
    book.set_account({"demo": True, "features": ["approvals"], "approved_positions": [], "currency": "USD"})
    rep = {"r": report()}
    monkeypatch.setattr(mgr, "Assistant", lambda conf: SimpleNamespace(analyze=lambda frames, spread, tf: rep["r"]))
    return SimpleNamespace(m=m, book=book, w=w, ex=ex, t=t, told=told, rep=rep)


def test_scan_proposes_once_per_closed_candle_and_skips_the_robot_market(setup):
    s = setup
    new = s.m.scan()
    assert len(new) == 1 and new[0]["symbol"] == "EURUSD" and new[0]["broker_symbol"] == "EURUSDm"
    assert new[0]["status"] == "PENDING" and new[0]["risk_reward"] == 2.0 and s.told == [("proposal", 1)]
    assert s.m.scan() == []                                         # same candle: not again
    s.w.push("XAUUSDm", None, {"1d": []}, 2)
    assert "XAUUSD" not in s.m.markets()                            # the gold robot's own market
    s.rep["r"] = report(action="WAIT")
    s.m.scanned.clear()
    s.m.proposals.clear()
    assert s.m.scan() == []


def test_approve_sends_one_order_and_the_robot_never_sees_its_result(setup):
    s = setup
    p = s.m.scan()[0]
    s.m.approve(p["id"])
    orders = s.book.fetch()
    assert len(orders) == 1 and orders[0]["kind"] == "OPEN_PLAN" and orders[0]["symbol"] == "EURUSDm"
    assert orders[0]["stop"] == 1.09 and orders[0]["target"] == 1.12 and orders[0]["risk"] == 10.0 and "owner" not in orders[0]
    s.book.report({"id": orders[0]["id"], "status": "FILLED", "price": 1.1001, "volume": 0.1, "tickets": [555], "time": NOW.isoformat()})
    assert s.book.take_results("robot") == []                       # the gold robot does not get it
    s.m.tick()
    assert s.m.proposals[p["id"]]["status"] == "OPEN" and s.m.proposals[p["id"]]["ticket"] == 555
    # followed through the account reports until it is gone from MT5
    pos = {"ticket": 555, "symbol": "EURUSDm", "side": "BUY", "volume": 0.1, "price_open": 1.1001, "sl": 1.09, "tp": 1.12,
           "profit": 20.0, "comment": "SPARKZ-A1", "price_current": 1.1199}
    s.book.set_account({"demo": True, "features": ["approvals"], "approved_positions": [pos], "currency": "USD"})
    s.m.tick()
    assert s.m.proposals[p["id"]]["profit"] == 20.0
    for _ in range(2):
        s.book.set_account({"demo": True, "features": ["approvals"], "approved_positions": [], "currency": "USD"})
        s.m.tick()
    q = s.m.proposals[p["id"]]
    assert q["status"] == "CLOSED" and q["close_reason"] == "take profit"
    assert s.m.snapshot()["summary"] == {"closed": 1, "won": 1, "net_profit": 20.0, "currency": "USD"}


@pytest.mark.parametrize("case, expect", [
    ("simulation", "EXECUTION_MODE=SIMULATION"), ("problem", "daily loss stop"), ("expired", "expired"),
    ("stale", "no fresh"), ("past_stop", "passed the stop loss"), ("drift", "chasing"), ("max_open", "already open"),
    ("old_bridge", "older version"),
])
def test_approve_refusals(setup, case, expect):
    s = setup
    p = s.m.scan()[0]
    if case == "simulation":
        s.m.executor_fn = lambda: None
    elif case == "problem":
        s.ex.problem = "daily loss stop: equity 3.2% below today's start"
    elif case == "expired":
        s.t[0] += 7 * 3600
    elif case == "stale":
        s.t[0] += 300
        s.book.set_account({"demo": True, "features": ["approvals"]})        # the account is fresh, the quote is not
    elif case == "past_stop":
        s.w.push("EURUSDm", {"time": NOW.isoformat(), "bid": 1.0890, "ask": 1.0891}, {}, 5)
    elif case == "drift":
        s.w.push("EURUSDm", {"time": NOW.isoformat(), "bid": 1.1060, "ask": 1.1061}, {}, 5)
    elif case == "max_open":
        s.m.cfg.max_open = 0
    elif case == "old_bridge":
        s.book.set_account({"demo": True, "approved_positions": []})
    with pytest.raises(ApprovalError, match=expect):
        s.m.approve(p["id"])
    assert s.book.fetch() == []                                     # nothing reached MT5


def test_reject_close_and_bridge_timeout(setup):
    s = setup
    p = s.m.scan()[0]
    s.m.reject(p["id"])
    with pytest.raises(ApprovalError):
        s.m.approve(p["id"])
    s.m.scanned.clear()
    s.t[0] += 60
    p2 = s.m.scan()[0]
    s.m.approve(p2["id"])
    s.t[0] += 40                                                   # the bridge never collected it
    s.m.tick()
    assert s.m.proposals[p2["id"]]["status"] == "FAILED"


def test_state_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "Assistant", lambda conf: SimpleNamespace(analyze=lambda f, sp, tf: report()))
    w = watch_with()
    bk = OrderBook(clock=lambda: NOW.timestamp())
    bk.set_account({"features": ["approvals"]})
    a = ApprovalManager(ApprovalConfig(), book=bk, watch=w, executor_fn=lambda: FakeExecutor(),
                        clock=lambda: NOW.timestamp(), path=tmp_path / "a.json")
    pid = a.scan()[0]["id"]
    a.approve(pid)
    b = ApprovalManager(ApprovalConfig(), book=OrderBook(), watch=w, executor_fn=lambda: None,
                        clock=lambda: NOW.timestamp(), path=tmp_path / "a.json")
    assert b.proposals[pid]["status"] == "FAILED" and b.seq == pid     # in flight at the restart: check MT5


def test_config_from_env():
    c = ApprovalConfig.from_env({"APPROVAL_TF": "4h", "APPROVAL_RISK": "25", "APPROVAL_MAX_OPEN": "99", "APPROVALS": "off"})
    assert (c.timeframe, c.risk, c.max_open, c.enabled, c.expiry_minutes) == ("4h", 25.0, 20, False, 120)
    assert ApprovalConfig.from_env({"APPROVAL_TF": "2h"}).timeframe == "1d"


# ---------------------------------------------------------------- the MT5 side (bridge/mt5_trader.py)
SPEC = importlib.util.spec_from_file_location("mt5_trader", Path(__file__).resolve().parents[2] / "bridge" / "mt5_trader.py")
mt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mt)


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO, POSITION_TYPE_BUY = 0, 0

    def __init__(self, info=None):
        self.sent, self.positions = [], []
        self.info = info or SimpleNamespace(trade_tick_value=1.0, trade_tick_size=0.00001, volume_min=0.01,
                                            volume_step=0.01, volume_max=100.0, digits=5, filling_mode=1, point=0.00001)

    def symbol_select(self, s, on): return s != "NOPE"
    def symbol_info(self, s): return self.info
    def symbol_info_tick(self, s): return SimpleNamespace(bid=1.1000, ask=1.1001)
    def account_info(self): return SimpleNamespace(trade_mode=0, trade_allowed=True)
    def terminal_info(self): return SimpleNamespace(trade_allowed=True)
    def last_error(self): return (1, "x")
    def positions_get(self, **k):
        return [p for p in self.positions if "ticket" not in k or p.ticket == k["ticket"]]

    def order_send(self, req):
        self.sent.append(req)
        return SimpleNamespace(retcode=10009, price=req["price"], volume=req["volume"], order=777, comment="done")


def trader(m, cap=0.1):
    return mt.Trader(m, "XAUUSDm", "http://x", "t", post=None, get=None, max_approval_lots=cap, log=lambda s: None)


def test_bridge_sizes_by_risk_and_sets_sl_tp_in_mt5():
    m = FakeMT5()
    r = trader(m, cap=1.0).execute({"id": 1, "kind": "OPEN_PLAN", "symbol": "EURUSDm", "side": "BUY", "stop": 1.0900,
                                   "target": 1.1200, "risk": 10.0, "proposal": 7, "age_seconds": 1})
    req = m.sent[0]
    # 0.0101 to the stop = 1010 ticks x $1 per lot -> 10 / 1010 = 0.0099 lots -> below 0.01, 0.01 risks 10.1 (< 1.5x)
    assert r["status"] == "FILLED" and req["volume"] == 0.01 and req["sl"] == 1.09 and req["tp"] == 1.12
    assert req["magic"] == mt.MAGIC_APPROVED and req["comment"] == "SPARKZ-A7"
    r = trader(FakeMT5(), cap=1.0).execute({"id": 2, "kind": "OPEN_PLAN", "symbol": "EURUSDm", "side": "BUY", "stop": 1.0900,
                                            "target": 1.12, "risk": 100.0, "proposal": 8, "age_seconds": 1})
    assert r["status"] == "FILLED" and r["volume"] == 0.09                  # 100 / 1010 = 0.099 -> 0.09


@pytest.mark.parametrize("order, msg", [
    ({"symbol": "NOPE"}, "not in this MT5"),
    ({"stop": 1.1050}, "no longer between"),
    ({"risk": 2.0}, "smallest lot"),
    ({"age_seconds": 99}, "old"),
])
def test_bridge_refuses_unsafe_approved_orders(order, msg):
    m = FakeMT5()
    o = {"id": 3, "kind": "OPEN_PLAN", "symbol": "EURUSDm", "side": "BUY", "stop": 1.09, "target": 1.12, "risk": 10.0,
         "proposal": 9, "age_seconds": 1, **order}
    r = trader(m).execute(o)
    assert r["status"] == "REJECTED" and msg in r["message"] and not m.sent


def test_bridge_caps_lots_and_closes_only_its_own_tickets():
    m = FakeMT5()
    r = trader(m, cap=0.05).execute({"id": 4, "kind": "OPEN_PLAN", "symbol": "EURUSDm", "side": "SELL", "stop": 1.1101,
                                     "target": 1.08, "risk": 500.0, "proposal": 1, "age_seconds": 0})
    assert r["volume"] == 0.05 and m.sent[0]["sl"] == 1.1101
    m.positions = [SimpleNamespace(ticket=5, magic=mt.MAGIC, symbol="XAUUSDm", type=0, volume=0.01),
                   SimpleNamespace(ticket=6, magic=mt.MAGIC_APPROVED, symbol="EURUSDm", type=0, volume=0.05)]
    t = trader(m)
    assert "already closed" in t.execute({"id": 5, "kind": "CLOSE_TICKET", "ticket": 5, "age_seconds": 0})["message"]
    r = t.execute({"id": 6, "kind": "CLOSE_TICKET", "ticket": 6, "age_seconds": 0})
    assert r["status"] == "FILLED" and m.sent[-1]["position"] == 6 and m.sent[-1]["type"] == 1


# ---------------------------------------------------------------- Telegram buttons
class FakeHttp:
    def __init__(self):
        self.calls = []

    def post(self, url, json):
        self.calls.append((url.rsplit("/", 1)[-1], json))
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": []})


def test_telegram_buttons_only_from_your_chat(setup):
    s = setup
    p = s.m.scan()[0]
    http = FakeHttp()
    bot = TelegramApprovals("123:abc", "42", s.m, http=http)
    bot.proposal(p)
    sent = http.calls[0][1]
    assert sent["chat_id"] == "42" and sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"ap:{p['id']}"
    assert "Nothing is placed unless you approve" in proposal_text(p)
    stranger = {"callback_query": {"id": "q1", "data": f"ap:{p['id']}", "message": {"chat": {"id": 99}, "message_id": 1}}}
    assert bot.handle(stranger) == "ignored: another chat" and s.book.fetch() == []
    mine = {"callback_query": {"id": "q2", "data": f"ap:{p['id']}", "message": {"chat": {"id": 42}, "message_id": 1}}}
    assert bot.handle(mine).startswith("Approved") and len(s.book.fetch()) == 1
    assert all("123:abc" not in str(j) for _, j in http.calls)          # the token never goes in a message
    again = bot.handle(mine)
    assert "not placed" in again                                       # a second press does nothing


def test_approvals_api(monkeypatch):
    import os
    from fastapi.testclient import TestClient
    os.environ["MARKET_DATA_PROVIDER"] = "MOCK"
    monkeypatch.setenv("SPARKZ_NO_APPROVAL_LOOP", "1")
    from app.main import app
    with TestClient(app) as c:
        d = c.get("/api/approvals").json()
        assert d["trading"] is False and d["config"]["timeframe"] == "1d" and d["pending"] == []
        assert c.post("/api/approvals/99/approve").status_code == 409
        assert c.post("/api/approvals/scan").json() == {"new": []}
    os.environ.pop("MARKET_DATA_PROVIDER", None)
