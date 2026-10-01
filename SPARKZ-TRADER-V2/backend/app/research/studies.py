"""
The studies behind the technical report (reports/TECHNICAL_REPORT.md).

  python -m app.research.studies            writes reports/studies/*.json and prints a summary

1. every preset on stored XAUUSD 15m, under each intrabar order
2. the same on the period covered by 5m candles, with the 5m candles as the intrabar path
3. execution-cost stress on stored 15m
4. synthetic stress suite + critical failure scenario (atr_grid, video_style)
5. walk-forward: 15m (atr_grid, video_style) and 1h (atr_grid)
6. parameter lab on 15m (atr_grid)
"""

from __future__ import annotations

import json
import time

from app.backtest.engine import run_backtest
from app.backtest.metrics import compute_metrics
from app.backtest.stress_test import execution_stress, run_all
from app.backtest.walk_forward import parameter_lab, walk_forward
from app.config import REPORTS_DIR, list_presets, load_settings
from app.market.history import load_history

OUT = REPORTS_DIR / "studies"
ORDERS = ["FAVOURABLE_FIRST", "RANDOM", "ADVERSE_FIRST"]


def _s(preset, **ov):
    return load_settings(preset, env={}, overrides=ov or None)


def _m(res):
    r = res.robot
    m = compute_metrics(r.completed, r.equity_curve, r.ledger.fills, res.settings.risk.initial_capital, r.inst,
                        res.settings.market.timeframe, res.candles)
    keep = ["start", "end", "net_pnl", "return_pct", "baskets", "win_rate_pct", "profit_factor", "avg_basket_profit",
            "avg_basket_loss", "largest_basket_loss", "max_drawdown_pct", "worst_floating_pnl", "max_consecutive_losses",
            "max_positions_in_basket", "max_notional_usd", "max_margin_usage_pct", "close_reasons", "by_regime",
            "mae_mfe", "costs", "buy_and_hold"]
    return {k: m[k] for k in keep} | {"halted": r.halted}


def save(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str))


def main():
    t0 = time.time()
    c15, c5, c1h = (load_history("XAUUSD", tf) for tf in ("15m", "5m", "1h"))
    presets = list_presets()

    out = {p: {o: _m(run_backtest(_s(p, execution={"intrabar_order": o}), c15)) for o in ORDERS} for p in presets}
    save("presets_15m", {"data": f"Yahoo GC=F 15m, {len(c15)} candles", "results": out})
    print("presets_15m", round(time.time() - t0))

    start = int((c15["timestamp"] < c5["timestamp"].min()).sum()) + 1
    out5 = {p: {o: {"ohlc_path": _m(run_backtest(_s(p, execution={"intrabar_order": o}), c15, start=start)),
                    "5m_path": _m(run_backtest(_s(p, execution={"intrabar_order": o}), c15, start=start, path_candles=c5))}
                for o in ORDERS} for p in presets}
    save("path_resolution", {"data": f"15m decisions from {c15['timestamp'].iloc[start]}; 5m candles as path", "results": out5})
    print("path_resolution", round(time.time() - t0))

    save("execution_stress_15m", {p: execution_stress(_s(p), c15, "XAUUSD 15m") for p in presets})
    print("execution_stress", round(time.time() - t0))

    for p in ("atr_grid", "video_style"):
        save(f"stress_{p}", run_all(_s(p), c15, "XAUUSD 15m"))
        print("stress", p, round(time.time() - t0))

    space15 = {"grid.atr_multiplier": [0.5, 1.0], "risk.max_positions": [3, 5]}
    save("walk_forward_15m_atr_grid", walk_forward(_s("atr_grid"), c15, space15, folds=3, label="XAUUSD 15m"))
    save("walk_forward_15m_video_style", walk_forward(_s("video_style"), c15, {"grid.distance": [2.0, 4.0]}, folds=3,
                                                      label="XAUUSD 15m"))
    save("walk_forward_1h_atr_grid", walk_forward(_s("atr_grid", market={"timeframe": "1h"}), c1h, space15, folds=4,
                                                  label="XAUUSD 1h"))
    save("walk_forward_1h_baseline", walk_forward(_s("baseline", market={"timeframe": "1h"}), c1h, None, folds=4,
                                                  label="XAUUSD 1h"))
    print("walk_forward", round(time.time() - t0))

    save("lab_15m_atr_grid", parameter_lab(_s("atr_grid"), c15, {"grid.atr_multiplier": [0.5, 1.0],
                                                                  "target.fixed_usd": [5, 10, 20]}, "XAUUSD 15m"))
    print("done", round(time.time() - t0))


if __name__ == "__main__":
    main()
