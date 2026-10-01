from datetime import datetime, timedelta, timezone

from app.paper.readiness import readiness
from app.paper.runner import PaperRunConfig, init_state
from app.paper.simulator import PaperTradeRecord

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)
TESTED = {"group": "tested", "label": "Passed research checks", "summary": "Beat random entries.",
          "break_even_round_trip_bp": 17.0}


def _state(name, pnls, days_ago=40):
    s = init_state(PaperRunConfig(name, "BTC-USD", "1h", "baseline", 10_000))
    start = NOW - timedelta(days=days_ago)
    for i, p in enumerate(pnls):
        t = start + timedelta(hours=10 * i)
        s.account.trade_history.append(PaperTradeRecord(
            symbol="BTC-USD", direction="BUY", entry_price=1.0, exit_price=1.0, size=1.0, pnl=p,
            opened_at=t, closed_at=t + timedelta(hours=5), reason="TARGET" if p > 0 else "STOP"))
    return s


def _status(r, name):
    return next(i["status"] for i in r["items"] if i["name"] == name)


def test_ready_when_every_condition_is_met():
    main, fee = _state("acct", [10.0] * 30), _state("acct_fee10", [5.0] * 30)
    others = {"acct": (main, {"verdict": "Passing"}), "acct_fee10": (fee, {"verdict": "Passing"})}
    r = readiness("acct", main, {"verdict": "Passing", "closed_trades": 30}, None, TESTED, others, NOW)
    assert r["status"] == "ready" and r["met"] == r["total"] == 5


def test_pending_while_collecting_evidence():
    s = _state("acct", [-1.0] * 3, days_ago=3)
    r = readiness("acct", s, {"verdict": "Collecting trades (3/30)", "closed_trades": 3}, None, TESTED, {"acct": (s, {})}, NOW)
    assert r["status"] == "pending"
    assert _status(r, "Paper verdict") == "pending" and _status(r, "Long enough") == "pending"
    assert _status(r, "After fees") == "na" and "17 bp" in r["items"][2]["detail"]


def test_not_ready_on_thin_research_or_losing_fee_copy():
    main, fee = _state("acct", [10.0] * 30), _state("acct_fee10", [-5.0] * 30)
    others = {"acct": (main, {}), "acct_fee10": (fee, {"verdict": "Collecting"})}
    thin = dict(TESTED, group="thin", label="Thin edge")
    r = readiness("acct", main, {"verdict": "Passing", "closed_trades": 30}, None, thin, others, NOW)
    assert r["status"] == "not_ready"
    assert _status(r, "Research checks") == "fail" and _status(r, "After fees") == "fail"


def test_unhealthy_account_is_not_ready():
    s = _state("acct", [10.0] * 30)
    s.halted = "2026-11-10 10:00 UTC: drawdown"
    r = readiness("acct", s, {"verdict": "Passing", "closed_trades": 30}, None, TESTED, {"acct": (s, {})}, NOW)
    assert _status(r, "Healthy now") == "fail" and r["status"] == "not_ready"


def test_fee_copies_get_no_checklist():
    s = _state("acct_fee10", [])
    r = readiness("acct_fee10", s, {}, None, TESTED, {}, NOW)
    assert r["status"] == "na" and r["items"] == [] and "0.10%" in r["note"]
