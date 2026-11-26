"""Trade history: every closed SPARKZ basket of the robot's market and execution mode, across restarts, with the
running total against the starting balance (the MT5 balance when the robot first synced to it)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.state import STATE, runner
from app.backtest.metrics import clean

router = APIRouter(prefix="/api/trades", tags=["trades"])


def _summary(trades: list[dict], start: float | None) -> dict:
    pnl = [t["pnl"] for t in trades]
    won = [p for p in pnl if p > 0]
    lost = [p for p in pnl if p <= 0]
    net = sum(pnl)
    return {"trades": len(pnl), "wins": len(won), "losses": len(lost),
            "win_rate_pct": round(100 * len(won) / len(pnl), 1) if pnl else None,
            "total_won": round(sum(won), 2), "total_lost": round(sum(lost), 2), "net": round(net, 2),
            "net_pct": round(100 * net / start, 2) if start else None,
            "best": round(max(pnl), 2) if pnl else None, "worst": round(min(pnl), 2) if pnl else None,
            "profit_factor": round(sum(won) / -sum(lost), 2) if lost and sum(lost) < 0 else None,
            "average": round(net / len(pnl), 2) if pnl else None}


@router.get("")
def trades(mode: str | None = Query(None, pattern="^(SIMULATION|MT5_DEMO|MT5_LIVE)$")):
    r = runner()
    s = r.s
    mode = mode or s.execution.mode
    symbol = s.market.symbol
    with r.lock:
        robot = r.robot
        current_mode = s.execution.mode
        mem = [b.to_dict() for b in robot.completed]
        open_basket = robot.snapshot().get("basket")
        account = robot.account.snapshot()
        mt5 = robot.executor.book.account_report() if getattr(robot, "live", False) else None
    hist = STATE.repo.trade_history(symbol, mode) if STATE.repo is not None else None
    if hist is None:                                   # no database: this run only
        hist = {"runs": 1, "baskets": mem if mode == current_mode else [], "first_run": None, "currency": "",
                "starting_balance": robot.account.initial_balance if mode == current_mode else None}
    start = hist["starting_balance"]
    risk_usd = s.risk.max_basket_loss_usd or s.risk.initial_capital * s.risk.max_basket_loss_percent / 100
    running = 0.0
    rows = []
    for b in hist["baskets"]:
        running += b["pnl"]
        rows.append({"uid": b["uid"], "direction": b["direction"], "opened_at": b["opened_at"], "closed_at": b["closed_at"],
                     "lots": b.get("total_lots"), "entry": b.get("avg_entry"), "exit": b.get("exit_price"),
                     "pnl": round(b["pnl"], 2), "r": round(b["pnl"] / risk_usd, 2) if risk_usd else None,
                     "close_reason": b.get("close_reason"), "duration_minutes": b.get("duration_minutes"),
                     "running_total": round(running, 2),
                     "balance_after": round(start + running, 2) if start is not None else None})
    out = {"symbol": symbol, "mode": mode, "current_mode": current_mode, "runs": hist["runs"],
           "since": hist["first_run"], "currency": hist.get("currency") or (mt5 or {}).get("currency", ""),
           "starting_balance": start, "summary": _summary(rows, start), "r_unit_usd": risk_usd,
           "trades": rows[::-1],
           "open_trade": open_basket if mode == current_mode else None,
           "now": {"robot_balance": account.get("balance"), "robot_equity": account.get("equity"),
                   "mt5_balance": (mt5 or {}).get("balance"), "mt5_equity": (mt5 or {}).get("equity"),
                   "mt5_connected": mt5 is not None} if mode == current_mode else None}
    if out["now"] and mt5 and start is not None:
        out["now"]["mt5_change_since_start"] = round(mt5["balance"] - start, 2)
    return clean(out)
