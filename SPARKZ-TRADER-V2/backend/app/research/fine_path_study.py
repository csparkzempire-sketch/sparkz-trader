"""
Fine-path study: every preset on XAUUSD 15m decisions, with stored 1m (or 5m) candles as the intrabar
price path, under each intrabar-order assumption, next to the plain 15m OHLC path.

  python -m app.research.fine_path_study [--source oanda|mt5|dukascopy] [--path 1m] [--start 2024-01-01]

With real 1m data the ordering assumption only matters inside each minute, so the three orderings
should nearly agree; whatever result remains is far closer to what the strategy would really have done.
Writes reports/studies/fine_path_<source>_<tf>.json.
"""

from __future__ import annotations

import argparse
import json
import time

from app.backtest.engine import run_backtest
from app.config import REPORTS_DIR, list_presets, load_settings
from app.market.history import load_history
from app.research.studies import ORDERS, _m


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="1m")
    ap.add_argument("--source", default="oanda", choices=["yahoo", "oanda", "mt5", "dukascopy"])
    ap.add_argument("--start", default=None, help="only trade from this date (YYYY-MM-DD)")
    ap.add_argument("--presets", default=",".join(list_presets()))
    a = ap.parse_args(argv)
    t0 = time.time()
    c15, fine = load_history("XAUUSD", "15m", a.source), load_history("XAUUSD", a.path, a.source)
    first = max(fine["timestamp"].min(), c15["timestamp"].min() if not a.start else
                __import__("pandas").Timestamp(a.start, tz="UTC"))
    start = max(int((c15["timestamp"] < first).sum()) + 1, 600)       # 600 bars of indicator warm-up
    out = {}
    for p in a.presets.split(","):
        out[p] = {}
        for o in ORDERS:
            s = load_settings(p, env={}, overrides={"execution": {"intrabar_order": o}})
            out[p][o] = {"ohlc_path": _m(run_backtest(s, c15, start=start)),
                         f"{a.path}_path": _m(run_backtest(s, c15, start=start, path_candles=fine))}
            r = out[p][o]
            print(f"{p:22s} {o:17s} 15m-OHLC {r['ohlc_path']['net_pnl']:>9.0f}   "
                  f"{a.path}-path {r[f'{a.path}_path']['net_pnl']:>9.0f}   ({time.time() - t0:.0f}s)", flush=True)
    meta = {"source": a.source, "decisions": "XAUUSD 15m", "path": a.path, "from": str(c15["timestamp"].iloc[start]),
            "to": str(c15["timestamp"].iloc[-1]), "path_candles": len(fine)}
    (REPORTS_DIR / "studies").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "studies" / f"fine_path_{a.source}_{a.path}.json").write_text(
        json.dumps({"meta": meta, "results": out}, indent=1, default=str))
    print(meta)


if __name__ == "__main__":
    main()
