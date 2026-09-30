"""
Validation of the entry signal on its own: one position per basket, no grid.

The grid study's single-position control made money on XAUUSD 1h. That was one
pass over one history, so here it gets the same scrutiny the grid got.

PASS CRITERIA, fixed before the study was run (all five must hold):
  1. walk-forward: out-of-sample net P&L > 0 in total, and in >= 3 of 4 test windows
  2. perturbations: >= 80% of 60 variants (random costs, latency, parameters) profitable
  3. beats chance: net P&L above the 95th percentile of 200 random-direction entries taken at
     the same moments, with the same exits (tests direction skill), AND above the 95th
     percentile of 200 random-timing always-BUY entries (tests "gold went up")
  4. every calendar year profitable
  5. still profitable at 2x spread

Runs use the configured strategy with the account-drawdown halt switched off,
so every test sees the whole history; the halted run is reported alongside.

  python -m app.research.entry_validation          -> reports/entry_validation_<date>.json
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.backtest.monte_carlo import perturbation_runs, sequence_tests
from app.backtest.parallel import run_many
from app.backtest.runner import prepare_features, run_backtest
from app.backtest.summary import pick
from app.backtest.walk_forward import period_stability
from app.config import REPORTS_DIR, Settings, TargetMode, load_preset
from app.data.repository import load_candles
from app.strategy.entry_engine import EntryDecision, evaluate_entry

N_RANDOM = 200


def control(symbol: str = "XAUUSD", tf: str = "1h", **extra) -> Settings:
    s = load_preset("0_control_single_position", {"market": {"symbol": symbol, "timeframe": tf}, **extra})
    return s.model_copy(update={"risk": s.risk.model_copy(update={"max_account_drawdown_percent": 100.0})})


def _metrics(res) -> dict:
    return pick(res.metrics) | {"by_direction": _by_direction(res.baskets)}


def _by_direction(baskets) -> dict:
    out = {}
    for d in ("BUY", "SELL"):
        p = [b.pnl for b in baskets if b.direction == d]
        out[d] = {"baskets": len(p), "net_pnl": float(sum(p)),
                  "win_rate_pct": (sum(x > 0 for x in p) / len(p) * 100) if p else None}
    return out


class random_direction:
    """Fires exactly when the real rules fire, but picks BUY or SELL at random. Picklable, so the
    benchmark runs can use worker processes."""

    def __init__(self, seed: int):
        self.rng = np.random.default_rng(seed)

    def __call__(self, row, cfg):
        d = evaluate_entry(row, cfg)
        if d.direction == "NONE":
            return d
        return EntryDecision("BUY" if self.rng.random() < 0.5 else "SELL", ["random direction"])


class random_long:
    """BUY on a random `p` share of eligible bars (not warm-up), ignoring the rules."""

    def __init__(self, seed: int, p: float):
        self.rng, self.p = np.random.default_rng(seed), p

    def __call__(self, row, cfg):
        if row["regime"] == "WARMUP":
            return EntryDecision("NONE")
        return EntryDecision("BUY", ["random long"]) if self.rng.random() < self.p else EntryDecision("NONE")


_BENCH: dict = {}


def _bench_init(settings_json: dict, features: pd.DataFrame) -> None:
    _BENCH["s"], _BENCH["f"] = Settings.model_validate(settings_json), features


def _bench_one(entry_fn) -> float:
    return run_backtest(_BENCH["s"], features=_BENCH["f"], entry_fn=entry_fn).metrics["net_pnl"]


def _run_entries(s: Settings, f: pd.DataFrame, fns: list) -> list[float]:
    import os
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max(1, (os.cpu_count() or 2) - 1), initializer=_bench_init,
                             initargs=(s.model_dump(mode="json"), f)) as pool:
        return list(pool.map(_bench_one, fns, chunksize=4))


def _pct(a) -> dict:
    a = np.asarray(a, dtype=float)
    return {f"p{q}": float(np.percentile(a, q)) for q in (5, 50, 95)} | {"max": float(a.max())}


def benchmarks(s: Settings, f: pd.DataFrame, signal_pnl: float) -> dict:
    rd = _run_entries(s, f, [random_direction(k) for k in range(N_RANDOM)])
    # match the real strategy's entry rate: fraction of eligible bars on which the rules fire
    fires = sum(evaluate_entry(r, s.entry).direction != "NONE" for r in f.to_dict("records") if r["regime"] != "WARMUP")
    rate = fires / max(1, int((f["regime"] != "WARMUP").sum()))
    rl = _run_entries(s, f, [random_long(10_000 + k, rate) for k in range(N_RANDOM)])
    return {
        "random_direction_same_timing": _pct(rd) | {"signal_percentile": float((np.asarray(rd) < signal_pnl).mean() * 100)},
        "random_timing_always_buy": _pct(rl) | {"signal_percentile": float((np.asarray(rl) < signal_pnl).mean() * 100),
                                                "entry_rate": rate},
        "buy_and_hold_1oz_usd": float(f["close"].iloc[-1] - f["close"].iloc[200]),
        "buy_and_hold_pct": float((f["close"].iloc[-1] / f["close"].iloc[200] - 1) * 100),
    }


def _window(s: Settings, a, b) -> Settings:
    return s.model_copy(update={"market": s.market.model_copy(update={"start": a.isoformat(), "end": b.isoformat()})})


def walk_forward_entry(s: Settings, f: pd.DataFrame, n_steps: int = 4) -> dict:
    """Parameters chosen on the train window only: target %, basket loss %, minimum ADX."""
    grid = []
    for tgt, loss, adx in itertools.product((0.25, 0.5, 1.0), (0.5, 1.0, 2.0), (20.0, 25.0)):
        grid.append(({"target_percent": tgt, "loss_percent": loss, "min_adx": adx}, s.model_copy(update={
            "target": s.target.model_copy(update={"mode": TargetMode.PERCENT, "percent": tgt}),
            "stop": s.stop.model_copy(update={"max_basket_loss_percent": loss}),
            "risk": s.risk.model_copy(update={"risk_per_cycle": loss / 100}),
            "entry": s.entry.model_copy(update={"min_trend_strength": adx})})))
    ts = f["timestamp"]
    first, last = ts.iloc[250], ts.iloc[-1]
    train_len = (last - first) * 0.5
    test_len = (last - first - train_len) / n_steps
    steps = []
    for k in range(n_steps):
        a = first + test_len * k
        b = a + train_len
        c = b + test_len
        train = run_many([_window(v, a, b) for _, v in grid], f)
        score = [(r["metrics"]["return_pct"] / max(0.5, abs(r["metrics"]["max_drawdown_pct"])))
                 if "metrics" in r and r["metrics"]["baskets"] >= 5 else -np.inf for r in train]
        best = int(np.argmax(score))
        test = run_many([_window(grid[best][1], b, c)], f, 1)[0]
        steps.append({"test": [b.isoformat(), c.isoformat()], "chosen": grid[best][0],
                      "in_sample_return_pct": train[best]["metrics"]["return_pct"],
                      "out_of_sample": pick(test["metrics"]) if "metrics" in test else {"error": test.get("error")}})
    oos = [st["out_of_sample"].get("net_pnl") or 0.0 for st in steps]
    return {"steps": steps, "oos_net_pnl": float(sum(oos)), "oos_profitable_steps": sum(x > 0 for x in oos)}


def validate(symbol: str = "XAUUSD", tf: str = "1h") -> dict:
    s = control(symbol, tf)
    candles = load_candles(symbol, tf)
    f = prepare_features(candles, s)
    base = run_backtest(s, features=f)
    halted = run_backtest(load_preset("0_control_single_position", {"market": {"symbol": symbol, "timeframe": tf}}), features=f)
    sig = base.metrics["net_pnl"]

    bench = benchmarks(s, f, sig)
    wf = walk_forward_entry(s, f)
    pert = perturbation_runs(s, f, n=60)
    years = period_stability(s, f, "YS")
    quarters = period_stability(s, f, "QS")
    spread2 = run_backtest(s.model_copy(update={"execution": s.execution.model_copy(update={"spread_multiplier": 2.0})}), features=f)
    spread3 = run_backtest(s.model_copy(update={"execution": s.execution.model_copy(update={"spread_multiplier": 3.0})}), features=f)
    long_only = run_backtest(s, features=f, entry_fn=lambda r, c: d if (d := evaluate_entry(r, c)).direction == "BUY" else EntryDecision("NONE"))

    criteria = {
        "1_walk_forward": wf["oos_net_pnl"] > 0 and wf["oos_profitable_steps"] >= 3,
        "2_perturbations_80pct_profitable": (pert["profitable_runs_pct"] or 0) >= 80,
        "3a_beats_random_direction_p95": sig > bench["random_direction_same_timing"]["p95"],
        "3b_beats_random_long_p95": sig > bench["random_timing_always_buy"]["p95"],
        "4_every_year_profitable": all((y.get("net_pnl") or 0) > 0 for y in years),
        "5_profitable_at_2x_spread": spread2.metrics["net_pnl"] > 0,
    }
    return {
        "symbol": symbol, "timeframe": tf, "bars": len(candles),
        "first": str(candles["timestamp"].iloc[0]), "last": str(candles["timestamp"].iloc[-1]),
        "signal": _metrics(base),
        "signal_with_10pct_halt": pick(halted.metrics),
        "long_only_signal": _metrics(long_only),
        "sequence": sequence_tests([b.pnl for b in base.baskets], s.risk.initial_capital, 10.0),
        "benchmarks": bench, "walk_forward": wf, "perturbation": pert,
        "years": years, "quarters": quarters,
        "spread_2x": pick(spread2.metrics), "spread_3x": pick(spread3.metrics),
        "criteria": criteria, "passed": all(criteria.values()),
    }


def main() -> None:
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "xauusd_1h": validate("XAUUSD", "1h")}
    # cross-checks at the other timeframe and markets (smaller: benchmarks only where cheap)
    out["other_samples"] = {}
    for sym, tf in [("XAUUSD", "15m"), ("GBPUSD", "1h"), ("EURUSD", "1h"), ("USDJPY", "1h"), ("BTCUSD", "1h")]:
        s = control(sym, tf, sizing={"base_lot_mode": "ATR_NORMALIZED", "usd_per_atr": 10.0}) if sym != "XAUUSD" else control(sym, tf)
        f = prepare_features(load_candles(sym, tf), s)
        r = run_backtest(s, features=f)
        out["other_samples"][f"{sym} {tf}"] = _metrics(r)
    path = REPORTS_DIR / f"entry_validation_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(f"wrote {path}")
    print(json.dumps(out["xauusd_1h"]["criteria"], indent=1), "PASSED" if out["xauusd_1h"]["passed"] else "FAILED")


if __name__ == "__main__":
    main()
