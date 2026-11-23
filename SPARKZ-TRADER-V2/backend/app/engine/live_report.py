"""
Demo / live results against the backtests: is the robot doing on the MT5 account what the studies predicted?

GET /api/execution/report gives:
- execution quality of every MT5 fill (only with EXECUTION_MODE=MT5_DEMO / MT5_LIVE): the price quoted when the
  order was made against the price MT5 filled (slippage, positive = worse for the robot), the spread at the
  time, and the delay from decision to fill;
- the robot's closed baskets: count, win rate, net USD, average result in R (R = the basket loss limit);
- the reference from history (reports/studies/assistant_<source>.json): the assistant's win rate and average R
  over 8 months of real candles at a 0.54 spread, and the spread the studies assumed. The study's exits (the
  assistant's stop, a 2R target) differ from the preset's basket target and loss limit, so the comparison is a
  rough check, not a like-for-like test;
- a plain verdict once there are enough trades (30 or more).
"""

from __future__ import annotations

import json

import numpy as np

from app.config import REPORTS_DIR

MIN_TRADES = 30


def _stats(values) -> dict:
    a = np.asarray([v for v in values if v is not None], float)
    if not len(a):
        return {"count": 0}
    return {"count": int(len(a)), "mean": round(float(a.mean()), 4), "median": round(float(np.median(a)), 4),
            "p90": round(float(np.quantile(a, 0.9)), 4), "max": round(float(a.max()), 4)}


def reference() -> dict | None:
    for src in ("dukascopy", "mt5", "yahoo"):
        f = REPORTS_DIR / "studies" / f"assistant_{src}.json"
        if f.exists():
            o = json.loads(f.read_text())["overall"]
            m = json.loads(f.read_text())["meta"]
            return {"source": f.name, "trades": o.get("trades"), "win_rate_pct": o.get("win_rate_pct"),
                    "expectancy_r": o.get("expectancy_r"), "gross_expectancy_r": o.get("gross_expectancy_r"),
                    "spread_assumed": m.get("spread"), "period": f"{m.get('first_decision')} .. {m.get('last_decision')}"}
    return None


def build(robot) -> dict:
    s = robot.s
    risk_usd = s.risk.max_basket_loss_usd or s.risk.initial_capital * s.risk.max_basket_loss_percent / 100
    done = robot.completed
    pnl = [b.pnl for b in done]
    r = [p / risk_usd for p in pnl] if risk_usd else []
    results = {"baskets": len(done), "wins": int(sum(p > 0 for p in pnl)),
               "win_rate_pct": round(100 * float(np.mean([p > 0 for p in pnl])), 1) if pnl else None,
               "net_usd": round(float(sum(pnl)), 2), "avg_usd": round(float(np.mean(pnl)), 2) if pnl else None,
               "avg_r": round(float(np.mean(r)), 3) if r else None, "r_unit_usd": risk_usd,
               "by_close_reason": {k: sum(b.close_reason == k for b in done) for k in sorted({b.close_reason for b in done})}}
    ex = getattr(robot.executor, "executions", None)
    execution = None
    if ex is not None:
        execution = {"fills": len(ex), "slippage": _stats(e["slippage"] for e in ex),
                     "spread_at_order": _stats(e["spread_at_order"] for e in ex),
                     "delay_seconds": _stats(e["delay_seconds"] for e in ex), "recent": ex[-20:]}
    ref = reference()
    if len(done) < MIN_TRADES:
        verdict = f"{len(done)} closed basket(s): at least {MIN_TRADES} are needed before the results say anything."
    else:
        avg = results["avg_r"]
        exp = ref["expectancy_r"] if ref else None
        verdict = (f"{len(done)} baskets, average {avg:+.3f} R" + (f" against {exp:+.3f} R in the history study"
                   if exp is not None else "") + (": losing, as the studies predicted." if avg < 0 else
                   ": positive so far; with this few trades it can still be luck (the studies found no edge)."))
    if execution and execution["spread_at_order"].get("count"):
        sp = execution["spread_at_order"]["median"]
        assumed = ref["spread_assumed"] if ref else None
        if assumed:
            verdict += f" Real spread at order time: median {sp:g} (studies assumed {assumed:g})."
    return {"mode": s.execution.mode, "results": results, "execution": execution, "history_reference": ref,
            "verdict": verdict}
