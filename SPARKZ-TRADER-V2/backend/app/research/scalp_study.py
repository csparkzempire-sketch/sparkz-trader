"""
Structure-scalping study: the setup in scalp.py over a small parameter grid, chosen on an in-sample period
and judged on the out-of-sample period that follows it.

  python -m app.research.scalp_study [--source dukascopy] [--split 2025-01-01] [--spread 0.54] [--tfs 1m,5m]
  python -m app.research.scalp_study --tfs 15m,1h --min-trades 50       # intraday swing setups

  in-sample       candles before --split: every combination is run, the one with the best net expectancy
                  (R per trade, with at least --min-trades trades) is selected
  out-of-sample   candles from --split: the selected combination is reported, and every other combination
                  alongside it, so the choice can be seen in context

Each setup timeframe gets its own bias timeframe, stop window, holding limit and risk cap (TF_SETTINGS): a 1h
setup reads a 4h bias, may hold for two days and accepts a wider stop than a 1m scalp.

Writes reports/studies/scalp_structure_<source>.json (other --tfs: ..._<tfs>.json). Simulation only.
"""

from __future__ import annotations

import argparse
import itertools
import json
import time
from dataclasses import replace

import pandas as pd

from app.config import REPORTS_DIR
from app.market.history import load_history
from app.research.scalp import ScalpParams, params_dict, signals, simulate, summarize

GRID = {"swing_n": [2, 3, 5], "variant": ["choch", "sweep_choch"]}
# per setup timeframe: bias timeframe, stop/sweep window (setup candles), max hold, max risk (price), R targets
TF_SETTINGS = {
    "1m": {"htf": "15m", "lookback": 30, "max_hold_min": 120, "max_risk": 15.0, "rr": [1.0, 1.5, 2.0]},
    "5m": {"htf": "15m", "lookback": 30, "max_hold_min": 120, "max_risk": 15.0, "rr": [1.0, 1.5, 2.0]},
    "15m": {"htf": "1h", "lookback": 20, "max_hold_min": 8 * 60, "max_risk": 40.0, "rr": [1.5, 2.0, 3.0]},
    "1h": {"htf": "4h", "lookback": 12, "max_hold_min": 48 * 60, "max_risk": 80.0, "rr": [1.5, 2.0, 3.0]},
}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="dukascopy", choices=["yahoo", "oanda", "mt5", "dukascopy"])
    ap.add_argument("--split", default="2025-01-01", help="first out-of-sample date")
    ap.add_argument("--spread", type=float, default=0.54)
    ap.add_argument("--min-trades", type=int, default=100)
    ap.add_argument("--tfs", default="1m,5m", help="setup timeframes, e.g. 15m,1h")
    a = ap.parse_args(argv)
    t0 = time.time()
    m1 = load_history("XAUUSD", "1m", a.source).reset_index(drop=True)
    split = pd.Timestamp(a.split, tz="UTC")
    base = ScalpParams(spread=a.spread)
    rows = []
    tfs = a.tfs.split(",")
    for tf, n, variant in itertools.product(tfs, GRID["swing_n"], GRID["variant"]):
        cfg = TF_SETTINGS[tf]
        p0 = replace(base, tf=tf, swing_n=n, variant=variant, htf=cfg["htf"], lookback=cfg["lookback"],
                     max_hold_min=cfg["max_hold_min"], max_risk=cfg["max_risk"])
        sig = signals(m1, p0)
        for rr in cfg["rr"]:
            p = replace(p0, rr=rr)
            tr = simulate(m1, sig, p)
            ins = tr[tr["entry_time"] < split] if len(tr) else tr
            oos = tr[tr["entry_time"] >= split] if len(tr) else tr
            rows.append({"params": {"tf": tf, "swing_n": n, "variant": variant, "rr": rr},
                         "in_sample": summarize(ins, p), "out_of_sample": summarize(oos, p)})
            r_in, r_out = rows[-1]["in_sample"], rows[-1]["out_of_sample"]
            print(f"{tf} n={n} {variant:11s} rr={rr:<3}  IS {r_in.get('trades', 0):5d} tr  "
                  f"{r_in.get('expectancy_r', 0):+.3f}R (gross {r_in.get('gross_expectancy_r', 0):+.3f})   "
                  f"OOS {r_out.get('trades', 0):5d} tr  {r_out.get('expectancy_r', 0):+.3f}R "
                  f"(gross {r_out.get('gross_expectancy_r', 0):+.3f})  {r_out.get('net_usd', 0):+8.0f} USD  "
                  f"({time.time() - t0:.0f}s)", flush=True)
    ok = [r for r in rows if r["in_sample"].get("trades", 0) >= a.min_trades]
    chosen = max(ok, key=lambda r: r["in_sample"]["expectancy_r"]) if ok else None
    meta = {"source": a.source, "symbol": "XAUUSD", "first": str(m1["timestamp"].min()),
            "last": str(m1["timestamp"].max()), "split": str(split), "min_trades": a.min_trades,
            "selection": "best in-sample net expectancy (R per trade)", "grid": GRID,
            "tf_settings": {tf: TF_SETTINGS[tf] for tf in tfs}, "fixed_params": params_dict(base)}
    out = {"meta": meta, "chosen": chosen, "all": rows}
    (REPORTS_DIR / "studies").mkdir(parents=True, exist_ok=True)
    suffix = "" if tfs == ["1m", "5m"] else "_" + "-".join(tfs)
    (REPORTS_DIR / "studies" / f"scalp_structure_{a.source}{suffix}.json").write_text(json.dumps(out, indent=1, default=str))
    print("chosen:", json.dumps(chosen and {"params": chosen["params"],
                                             "in_sample": chosen["in_sample"]["expectancy_r"],
                                             "out_of_sample": chosen["out_of_sample"]}, default=str))


if __name__ == "__main__":
    main()
