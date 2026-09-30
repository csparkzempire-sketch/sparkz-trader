"""
The full research study behind reports/TECHNICAL_REPORT.md.

  python -m app.research.study            -> reports/study_<date>.json

1. XAUUSD 15m (the spec's primary market): strategies A-E and the control
   side by side, then for strategy B: stress tests, Monte Carlo, sensitivity,
   walk-forward and week-by-week stability.
2. XAUUSD 1h: the same comparison over ~2.4 years, walk-forward and
   month-by-month stability (15m history on Yahoo is only ~60 days).
3. Every other market (EURUSD, GBPUSD, USDJPY, BTCUSD) at 15m and 1h:
   the comparison, with strategy A's fixed step set to half that market's
   median ATR (a gold-sized step means nothing on EURUSD).
4. The ML study on XAUUSD 1h baskets.

Everything uses the default costs, risk limits and the pessimistic intrabar order.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from app.backtest.comparison import compare
from app.backtest.monte_carlo import perturbation_runs, sensitivity, sequence_tests
from app.backtest.runner import prepare_features, run_backtest
from app.backtest.stress_test import run_stress
from app.backtest.walk_forward import period_stability, walk_forward
from app.config import REPORTS_DIR, IntrabarMode, list_presets, load_preset
from app.data.repository import load_candles
from app.research.ml import ml_study

MARKETS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD"]


def _strip(cmp: dict) -> dict:
    for r in cmp["rows"]:
        r.pop("curve", None)
    return cmp


def _compare_market(symbol: str, tf: str) -> dict:
    candles = load_candles(symbol, tf)
    s = load_preset("B_atr_grid", {"market": {"symbol": symbol, "timeframe": tf}})
    atr = float(prepare_features(candles, s)["atr"].median())
    shared = {"market": {"symbol": symbol, "timeframe": tf}}
    keys = list(list_presets())
    out = compare([k for k in keys if k != "A_fixed_grid"], shared, candles=candles)
    a = compare(["A_fixed_grid"], {**shared, "grid": {"price_step": round(atr * 0.5, 6)}}, candles=candles)
    out["rows"].insert(1, {**a["rows"][0], "name": f"A: Fixed-size grid (step {atr * 0.5:.5g})"})
    out["median_atr"] = atr
    out["bars"] = len(candles)
    out["first"], out["last"] = str(candles["timestamp"].iloc[0]), str(candles["timestamp"].iloc[-1])
    return _strip(out)


def _deep_dive(symbol: str, tf: str, period: str) -> dict:
    s = load_preset("B_atr_grid", {"market": {"symbol": symbol, "timeframe": tf}})
    candles = load_candles(symbol, tf)
    f = prepare_features(candles, s)
    base = run_backtest(s, features=f)
    return {
        "baseline": {k: v for k, v in base.metrics.items() if k not in ("mae_mfe",)} | {"mae_mfe": {k: v for k, v in base.metrics["mae_mfe"].items() if k != "scatter"}},
        "stress": run_stress(s, candles),
        "sequence": sequence_tests([b.pnl for b in base.baskets], s.risk.initial_capital, s.risk.max_account_drawdown_percent),
        "perturbation": perturbation_runs(s, f, n=60),
        "sensitivity": sensitivity(s, f),
        "walk_forward": walk_forward(s, f, n_steps=4),
        "periods": period_stability(s, f, period),
        "intrabar_ohlc_path": {k: run_backtest(s.model_copy(update={"execution": s.execution.model_copy(update={"intrabar_mode": IntrabarMode.OHLC_PATH})}),
                                               features=f).metrics[k] for k in ("net_pnl", "win_rate_pct", "max_drawdown_pct", "baskets")},
    }


def main() -> None:
    t0 = time.time()
    out: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    out["comparison"] = {f"{sym} {tf}": _compare_market(sym, tf) for sym in MARKETS for tf in ("15m", "1h")}
    print(f"comparisons done ({time.time() - t0:.0f}s)")
    out["xauusd_15m"] = _deep_dive("XAUUSD", "15m", "W-MON")
    print(f"XAUUSD 15m deep dive done ({time.time() - t0:.0f}s)")
    out["xauusd_1h"] = _deep_dive("XAUUSD", "1h", "MS")
    print(f"XAUUSD 1h deep dive done ({time.time() - t0:.0f}s)")
    out["ml_xauusd_1h"] = ml_study(load_preset("B_atr_grid", {"market": {"symbol": "XAUUSD", "timeframe": "1h"}}))
    path = REPORTS_DIR / f"study_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(f"wrote {path} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
