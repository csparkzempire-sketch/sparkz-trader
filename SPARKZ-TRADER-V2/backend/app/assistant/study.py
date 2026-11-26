"""
How good are the assistant's calls on history? Replays the assistant over stored 1m candles and trades every
BUY/SELL it gives, one at a time, at the real spread.

  python -m app.assistant.study [--source dukascopy] [--start 2025-01-01] [--every 4] [--spread 0.54]

  - candles for 1m, 5m, 15m, 1h, 4h and 1d are built from the 1m history; at each decision time the assistant
    sees, on every timeframe, only candles that have closed (the same no-look-ahead rule as live);
  - a decision every `--every` 15m candles (4 = hourly) with the live settings (min_confidence, execution
    timeframe 15m); nothing is tuned on the data;
  - each BUY/SELL enters at the next 1m open, stop from the assistant's plan, target 2R, time stop 8 hours,
    exits checked on every 1m candle at the bid/ask (scalp.simulate);
  - reported overall, by confidence band, by the plan's strategy and by quarter, with the result before
    costs, so the assistant's calls can be compared with a coin toss (0 R before costs).

Slower timeframes over longer history (hourly candles, e.g. Dukascopy 2008-2026):
  python -m app.assistant.study --base-tf 1h --execution-tf 4h --every 1 --max-hold-hours 120 --max-risk 400
  candles 1h, 4h and 1d are built from the 1h history; exits are checked on 1h candles (stop first when a candle
  touches both); overnight swap is not charged.

Writes reports/studies/assistant_<source>.json (with --base-tf 1h: assistant_<source>_<execution tf>.json).
Simulation only.
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

from app.assistant.context import TF_MINUTES, build, resample
from app.assistant.engine import TF_ORDER, Assistant
from app.config import REPORTS_DIR
from app.market.history import load_history
from app.research.scalp import ScalpParams, simulate, summarize


def contexts(m1: pd.DataFrame, base_tf: str = "1m") -> dict:
    ctx = {}
    for tf in TF_ORDER:
        if TF_MINUTES[tf] < TF_MINUTES[base_tf]:
            continue
        df = m1 if tf == base_tf else resample(m1, tf)
        ctx[tf] = build(df, tf)
    return ctx


_CLOSES: dict[int, np.ndarray] = {}


def index_at(ctx, t: pd.Timestamp) -> int:
    """Last row whose candle has closed by time t."""
    close = _CLOSES.get(id(ctx))
    if close is None:
        close = _CLOSES[id(ctx)] = (ctx.f["timestamp"].dt.tz_convert(None).to_numpy()
                                    + np.timedelta64(ctx.minutes, "m"))
    return int(np.searchsorted(close, t.tz_convert(None).to_datetime64(), side="right")) - 1


def run_study(base: pd.DataFrame, base_tf: str = "1m", execution_tf: str = "15m", every: int = 4, spread: float = 0.54,
              min_confidence: float = 0.55, max_hold_hours: float = 8.0, max_risk: float = 80.0, decision: str = "VOTE",
              top: int = 1, start: str | None = None, end: str | None = None, source: str = "", progress=None,
              trades_out: int = 0) -> dict:
    """The study on `base` candles (1m or 1h). `progress(text)` is called now and then; `trades_out` > 0 adds the
    latest that many trades to the result."""
    t0 = time.time()
    say = progress or (lambda m: None)
    base = base.reset_index(drop=True)
    ctx = contexts(base, base_tf)
    say("candles ready: " + ", ".join(f"{tf} {len(c)}" for tf, c in ctx.items()))
    eng = Assistant(min_confidence, decision=decision, top_strategies=top)
    ex = execution_tf
    c15 = ctx[ex]
    warm = pd.Timedelta(days=30 if base_tf == "1m" else 365)
    first = pd.Timestamp(start, tz="UTC") if start else c15.f["timestamp"].iat[0] + warm
    times = (c15.f["timestamp"] + pd.Timedelta(minutes=TF_MINUTES[ex]))
    times = times[times >= first]
    if end:
        times = times[times < pd.Timestamp(end, tz="UTC")]
    times = times.iloc[::every]
    rows = []
    for n, t in enumerate(times):
        idx = {tf: i for tf, c in ctx.items() if (i := index_at(c, t)) >= 60}    # as live: a timeframe needs 60 candles
        if ex not in idx:
            continue
        rep = eng.analyze_at({tf: ctx[tf] for tf in idx}, idx, spread, ex, with_history=False)
        if rep["action"] in ("BUY", "SELL") and rep["plan"]:
            p = rep["plan"]
            rows.append({"time": t, "dir": 1 if rep["action"] == "BUY" else -1, "stop": p["stop"],
                         "confidence": rep["confidence_pct"], "strategy": p["strategy"], "tf": p["tf"]})
        if n % 250 == 0:
            say(f"{n}/{len(times)} decisions, {len(rows)} calls ({time.time() - t0:.0f} s)")
    sig = pd.DataFrame(rows)
    p = ScalpParams(spread=spread, rr=2.0, max_hold_min=int(max_hold_hours * 60), max_risk=max_risk)
    tr = simulate(base, sig[["time", "dir", "stop"]], p) if len(sig) else pd.DataFrame()
    out = {"meta": {"source": source, "first_decision": str(times.iloc[0]) if len(times) else None,
                    "last_decision": str(times.iloc[-1]) if len(times) else None, "decisions": int(len(times)),
                    "base_tf": base_tf, "execution_tf": ex, "every_execution_candles": every,
                    "spread": spread, "min_confidence": min_confidence,
                    "decision": decision if decision == "VOTE" else f"TOP_STRATEGY top {top}",
                    "exits": f"assistant's stop, 2R target, {max_hold_hours:g} h time stop, checked on "
                             f"{base_tf} candles at bid/ask",
                    "calls": int(len(sig))},
           "overall": summarize(tr, p) if len(tr) else {"trades": 0}}
    if len(tr):
        tr = tr.join(sig[["confidence", "strategy", "tf"]], on="signal")
        tr["quarter"] = pd.to_datetime(tr["entry_time"], utc=True).dt.tz_convert(None).dt.to_period("Q").astype(str)
        tr["band"] = pd.cut(tr["confidence"], [0, 65, 75, 85, 101], labels=["55-65", "65-75", "75-85", "85+"]).astype(str)
        tr["year"] = pd.to_datetime(tr["entry_time"], utc=True).dt.year.astype(str)
        for col in ("band", "strategy", "quarter", "year"):
            out[f"by_{col}"] = {str(k): {"trades": int(len(g)), "win_rate_pct": round(100 * float((g["r"] > 0).mean()), 1),
                                         "expectancy_r": round(float(g["r"].mean()), 3),
                                         "gross_expectancy_r": round(float(g["gross_r"].mean()), 3),
                                         "net_usd": round(float(g["pnl_usd"].sum()), 0)} for k, g in tr.groupby(col)}
        se = float(tr["r"].std() / np.sqrt(len(tr)))
        out["overall"]["standard_error_r"] = round(se, 3)
        out["overall"]["t_stat"] = round(float(tr["r"].mean()) / se, 2) if se > 0 else None
        if trades_out:
            eq = tr["pnl_usd"].cumsum()
            out["equity_curve"] = [{"time": str(t), "equity": round(float(e), 2)}
                                   for t, e in zip(tr["exit_time"], eq)]
            last = tr.tail(trades_out).iloc[::-1]
            out["trades"] = [{"entry_time": str(r.entry_time), "exit_time": str(r.exit_time),
                              "side": "BUY" if r.dir == 1 else "SELL", "entry": round(float(r.entry), 5),
                              "stop": round(float(r.stop), 5), "target": round(float(r.target), 5),
                              "exit": round(float(r.exit), 5), "r": round(float(r.r), 3),
                              "pnl_usd": round(float(r.pnl_usd), 2), "exit_reason": r.reason,
                              "strategy": r.strategy, "tf": r.tf, "confidence": r.confidence} for r in last.itertuples()]
    say(f"done: {out['overall'].get('trades', 0)} trades ({time.time() - t0:.0f} s)")
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="dukascopy", choices=["yahoo", "oanda", "mt5", "dukascopy"])
    ap.add_argument("--start", default=None, help="first decision date (default: 30 days into the data)")
    ap.add_argument("--every", type=int, default=4, help="decide every N 15m candles")
    ap.add_argument("--spread", type=float, default=0.54)
    ap.add_argument("--min-confidence", type=float, default=0.55)
    ap.add_argument("--base-tf", default="1m", choices=["1m", "1h"])
    ap.add_argument("--execution-tf", default="15m", choices=["15m", "1h", "4h", "1d"])
    ap.add_argument("--max-hold-hours", type=float, default=8.0)
    ap.add_argument("--max-risk", type=float, default=80.0)
    ap.add_argument("--decision", default="VOTE", choices=["VOTE", "TOP_STRATEGY"])
    ap.add_argument("--top", type=int, default=1, help="TOP_STRATEGY: how many of the top-ranked strategies to consider")
    a = ap.parse_args(argv)
    t0 = time.time()
    base = load_history("XAUUSD", a.base_tf, a.source)
    out = run_study(base, a.base_tf, a.execution_tf, a.every, a.spread, a.min_confidence, a.max_hold_hours, a.max_risk,
                    a.decision, a.top, a.start, source=a.source, progress=lambda m: print("  " + m, flush=True))
    (REPORTS_DIR / "studies").mkdir(parents=True, exist_ok=True)
    name = f"assistant_{a.source}" + ("" if a.base_tf == "1m" else f"_{a.execution_tf}") + \
        ("" if a.decision == "VOTE" else f"_top{a.top}")
    (REPORTS_DIR / "studies" / f"{name}.json").write_text(json.dumps(out, indent=1, default=str))
    o = out["overall"]
    print(f"{o.get('trades', 0)} trades, win {o.get('win_rate_pct')}%, {o.get('expectancy_r')}R net "
          f"(gross {o.get('gross_expectancy_r')}R), t={o.get('t_stat')}, net {o.get('net_usd')} USD "
          f"({time.time() - t0:.0f}s)")
    for k in ("by_band", "by_strategy"):
        print(k, json.dumps(out.get(k), default=str))


if __name__ == "__main__":
    main()
