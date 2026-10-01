"""
Live-trading readiness checklist for each paper account.

Five conditions, fixed in advance. An account counts as ready for a small live test
only when every condition that applies is met:

  1. Paper verdict      the 30-trade evaluation says Passing (app.paper.evaluation).
  2. Research checks    the strategy beat random entries and survives 2x trading costs
                        (research group "tested", app.research.verdicts).
  3. After fees         if a fee copy of the account exists (name + "_fee<bps>"), it is
                        profitable over at least MIN_TRADES closed trades. Without a fee copy
                        this doesn't apply: compare your broker's costs with the break-even.
  4. Long enough        the first paper trade is at least MIN_DAYS old, so a short lucky
                        run can't qualify.
  5. Healthy now        not halted, losses within the backtest's normal range, and the early
                        check not "very unusual".

Fee copies are measurement accounts, so they get no checklist of their own.

Information only: nothing here places orders or changes trading. Live trading stays
disabled in this app; a "ready" account is a candidate for a small, separately set-up
live test, not an instruction to trade.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

MIN_TRADES = 30
MIN_DAYS = 28
FEE_COPY = re.compile(r"^(?P<base>.+)_fee(?P<bps>\d+)$")


def _item(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def readiness(name: str, state, evaluation: dict | None, norms: dict | None, research: dict | None,
              others: dict[str, tuple], now: datetime | None = None) -> dict:
    """`others` maps account name -> (state, evaluation) for every account, to find fee copies."""
    now = now or datetime.now(timezone.utc)
    m = FEE_COPY.match(name)
    if m:
        return {"status": "na", "label": f"Fee copy of {m['base']}", "met": 0, "total": 0, "items": [],
                "note": f"Measures what a {int(m['bps']) / 100:.2f}% fee per side does to {m['base']}."}

    ev = evaluation or {}
    trades = state.account.trade_history
    items = []

    verdict = ev.get("verdict") or "No targets set"
    n = ev.get("closed_trades", len(trades))
    if verdict == "Passing":
        items.append(_item("Paper verdict", "pass", f"Passing after {n} trades."))
    elif verdict.startswith("Failing"):
        items.append(_item("Paper verdict", "fail", verdict + "."))
    else:
        items.append(_item("Paper verdict", "pending", f"{n} of {MIN_TRADES} trades closed."))

    if research is None:
        items.append(_item("Research checks", "pending", "No research results for this setup yet."))
    elif research["group"] == "tested":
        items.append(_item("Research checks", "pass", research["summary"]))
    else:
        items.append(_item("Research checks", "fail", research["label"] + ". " + research["summary"]))

    copy = next(((k, v) for k, v in others.items() if (fm := FEE_COPY.match(k)) and fm["base"] == name), None)
    if copy is None:
        be = research and research.get("break_even_round_trip_bp")
        items.append(_item("After fees", "na", "No fee copy. Check your broker's round-trip cost"
                           + (f" is well below the break-even of about {be:.0f} bp." if be else " against the backtest.")))
    else:
        cname, (cstate, cev) = copy
        cn = len(cstate.account.trade_history)
        cpnl = sum(t.pnl for t in cstate.account.trade_history)
        cverdict = (cev or {}).get("verdict", "")
        if cn < MIN_TRADES:
            items.append(_item("After fees", "pending", f"{cname}: {cn} of {MIN_TRADES} trades, net {cpnl:+.2f} so far."))
        elif cpnl > 0 and not cverdict.startswith("Failing"):
            items.append(_item("After fees", "pass", f"{cname} is profitable after fees: net {cpnl:+.2f} over {cn} trades."))
        else:
            items.append(_item("After fees", "fail", f"{cname}: net {cpnl:+.2f} over {cn} trades"
                               + (f"; {cverdict}" if cverdict.startswith("Failing") else "") + "."))

    first = min((t.opened_at for t in trades), default=None)
    if first is None:
        items.append(_item("Long enough", "pending", f"No trades yet; needs {MIN_DAYS} days from the first."))
    else:
        days = (now - first).days
        items.append(_item("Long enough", "pass" if days >= MIN_DAYS else "pending",
                           f"{days} of {MIN_DAYS} days since the first trade."))

    problems = []
    if state.halted:
        problems.append("halted by the drawdown limit")
    if norms and norms.get("verdict", "").startswith("Outside"):
        problems.append("losses outside the backtest's normal range")
    if (ev.get("early_check") or {}).get("status") == "very_unusual":
        problems.append("early check very unusual")
    items.append(_item("Healthy now", "fail" if problems else "pass",
                       ("Problem: " + "; ".join(problems) + ".") if problems else
                       "Not halted, losses in normal range, early check fine."))

    applicable = [i for i in items if i["status"] != "na"]
    met = sum(i["status"] == "pass" for i in applicable)
    if met == len(applicable):
        status, label = "ready", "Ready for a small live test"
    elif any(i["status"] == "fail" for i in applicable):
        status, label = "not_ready", "Not ready"
    else:
        status, label = "pending", "Not yet: still collecting evidence"
    return {"status": status, "label": label, "met": met, "total": len(applicable), "items": items, "note": None}
