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

Writes reports/studies/assistant_<source>.json. Simulation only.
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


def contexts(m1: pd.DataFrame) -> dict:
    ctx = {}
    for tf in TF_ORDER:
        df = m1 if tf == "1m" else resample(m1, tf)
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


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="dukascopy", choices=["yahoo", "oanda", "mt5", "dukascopy"])
    ap.add_argument("--start", default=None, help="first decision date (default: 30 days into the data)")
    ap.add_argument("--every", type=int, default=4, help="decide every N 15m candles")
    ap.add_argument("--spread", type=float, default=0.54)
    ap.add_argument("--min-confidence", type=float, default=0.55)
    a = ap.parse_args(argv)
    t0 = time.time()
    m1 = load_history("XAUUSD", "1m", a.source).reset_index(drop=True)
    ctx = contexts(m1)
    print(f"contexts built ({time.time() - t0:.0f}s): " + ", ".join(f"{tf} {len(c)}" for tf, c in ctx.items()), flush=True)
    eng = Assistant(a.min_confidence)
    c15 = ctx["15m"]
    first = pd.Timestamp(a.start, tz="UTC") if a.start else c15.f["timestamp"].iat[0] + pd.Timedelta(days=30)
    times = (c15.f["timestamp"] + pd.Timedelta(minutes=15))
    times = times[times >= first].iloc[::a.every]
    rows = []
    for n, t in enumerate(times):
        idx = {tf: i for tf, c in ctx.items() if (i := index_at(c, t)) >= 60}    # as live: a timeframe needs 60 candles
        if "15m" not in idx:
            continue
        rep = eng.analyze_at({tf: ctx[tf] for tf in idx}, idx, a.spread, "15m", with_history=False)
        if rep["action"] in ("BUY", "SELL") and rep["plan"]:
            p = rep["plan"]
            rows.append({"time": t, "dir": 1 if rep["action"] == "BUY" else -1, "stop": p["stop"],
                         "confidence": rep["confidence_pct"], "strategy": p["strategy"], "tf": p["tf"]})
        if n % 500 == 0:
            print(f"  {n}/{len(times)} decisions, {len(rows)} trades ({time.time() - t0:.0f}s)", flush=True)
    sig = pd.DataFrame(rows)
    p = ScalpParams(spread=a.spread, rr=2.0, max_hold_min=8 * 60, max_risk=80.0)
    tr = simulate(m1, sig[["time", "dir", "stop"]], p) if len(sig) else pd.DataFrame()
    out = {"meta": {"source": a.source, "first_decision": str(times.iloc[0]) if len(times) else None,
                    "last_decision": str(times.iloc[-1]) if len(times) else None, "decisions": int(len(times)),
                    "every_15m_candles": a.every, "spread": a.spread, "min_confidence": a.min_confidence,
                    "exits": "assistant's stop, 2R target, 8 h time stop, checked on 1m candles at bid/ask",
                    "calls": int(len(sig))},
           "overall": summarize(tr, p) if len(tr) else {"trades": 0}}
    if len(tr):
        tr = tr.join(sig[["confidence", "strategy", "tf"]], on="signal")
        tr["quarter"] = pd.to_datetime(tr["entry_time"], utc=True).dt.tz_convert(None).dt.to_period("Q").astype(str)
        tr["band"] = pd.cut(tr["confidence"], [0, 65, 75, 85, 101], labels=["55-65", "65-75", "75-85", "85+"]).astype(str)
        for col in ("band", "strategy", "quarter"):
            out[f"by_{col}"] = {str(k): {"trades": int(len(g)), "win_rate_pct": round(100 * float((g["r"] > 0).mean()), 1),
                                         "expectancy_r": round(float(g["r"].mean()), 3),
                                         "gross_expectancy_r": round(float(g["gross_r"].mean()), 3),
                                         "net_usd": round(float(g["pnl_usd"].sum()), 0)} for k, g in tr.groupby(col)}
        se = float(tr["r"].std() / np.sqrt(len(tr)))
        out["overall"]["standard_error_r"] = round(se, 3)
        out["overall"]["t_stat"] = round(float(tr["r"].mean()) / se, 2) if se > 0 else None
    (REPORTS_DIR / "studies").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "studies" / f"assistant_{a.source}.json").write_text(json.dumps(out, indent=1, default=str))
    o = out["overall"]
    print(f"{o.get('trades', 0)} trades, win {o.get('win_rate_pct')}%, {o.get('expectancy_r')}R net "
          f"(gross {o.get('gross_expectancy_r')}R), t={o.get('t_stat')}, net {o.get('net_usd')} USD "
          f"({time.time() - t0:.0f}s)")
    for k in ("by_band", "by_strategy"):
        print(k, json.dumps(out.get(k), default=str))


if __name__ == "__main__":
    main()
