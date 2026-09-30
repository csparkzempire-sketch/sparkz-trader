"""
Walk-forward validation and period stability.

Walk-forward: the history is cut into consecutive windows. In each step the
parameters are chosen on a TRAIN window only (small grid: spacing, target,
max positions; score = return / |max drawdown|, needing at least 5 baskets),
then run untouched on the TEST window right after it. Only test windows count
toward the out-of-sample result. The gap between in-sample and out-of-sample
figures is the overfitting estimate.

Period stability: the same fixed configuration run separately on each
calendar period (fresh capital each time), to see whether behaviour holds up
across time or depends on one lucky stretch.

Indicators are computed once on the full history. That is safe: every feature
is trailing, and the engine only trades inside each window's dates.
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from app.backtest.parallel import run_many
from app.backtest.summary import pick
from app.config import GridMode, Settings, TargetMode


def _window(s: Settings, start, end) -> Settings:
    return s.model_copy(update={"market": s.market.model_copy(update={"start": start.isoformat(), "end": end.isoformat()})})


def _score(m: dict) -> float:
    if not m or m.get("baskets", 0) < 5:
        return -np.inf
    dd = abs(m.get("max_drawdown_pct") or 0) or 0.5
    return m["return_pct"] / dd


def _grid(s: Settings) -> list[tuple[dict, Settings]]:
    spacings = ([s.grid.price_step * k for k in (0.5, 1.0, 2.0)] if s.grid.mode == GridMode.PRICE
                else [s.grid.atr_multiplier * k for k in (0.5, 1.0, 2.0)])
    out = []
    for sp, tgt, mp in itertools.product(spacings, (0.25, 0.5, 1.0), (3, 5)):
        g = s.grid.model_copy(update={"price_step": sp} if s.grid.mode == GridMode.PRICE else {"atr_multiplier": sp})
        v = s.model_copy(update={"grid": g, "target": s.target.model_copy(update={"mode": TargetMode.PERCENT, "percent": tgt}),
                                 "risk": s.risk.model_copy(update={"max_positions": mp})})
        out.append(({"spacing": sp, "target_percent": tgt, "max_positions": mp}, v))
    return out


def walk_forward(settings: Settings, features: pd.DataFrame, n_steps: int = 4, train_frac: float = 0.5,
                 workers=None) -> dict:
    ts = features["timestamp"]
    first = ts.iloc[min(len(ts) - 1, 250)]           # leave room for indicator warm-up
    last = ts.iloc[-1]
    train_len = (last - first) * train_frac
    test_len = (last - first - train_len) / n_steps
    steps, oos_pnls = [], []
    for k in range(n_steps):
        tr_start = first + test_len * k
        tr_end = tr_start + train_len
        te_end = tr_end + test_len
        grid = _grid(settings)
        train = run_many([_window(v, tr_start, tr_end) for _, v in grid], features, workers)
        scores = [_score(r.get("metrics")) for r in train]
        best = int(np.argmax(scores))
        params, chosen = grid[best]
        test = run_many([_window(chosen, tr_end, te_end)], features, 1)[0]
        oos_pnls += test.get("basket_pnls", [])
        steps.append({
            "train": [tr_start.isoformat(), tr_end.isoformat()], "test": [tr_end.isoformat(), te_end.isoformat()],
            "chosen": params, "train_score": None if not np.isfinite(scores[best]) else scores[best],
            "in_sample": pick(train[best]["metrics"]) if "metrics" in train[best] else None,
            "out_of_sample": pick(test["metrics"]) if "metrics" in test else {"error": test.get("error")},
        })
    wins = [p for p in oos_pnls if p > 0]
    losses = [p for p in oos_pnls if p <= 0]
    is_ret = [s["in_sample"]["return_pct"] for s in steps if s["in_sample"]]
    oos_ret = [s["out_of_sample"].get("return_pct") for s in steps if s["out_of_sample"].get("return_pct") is not None]
    return {
        "steps": steps,
        "out_of_sample": {
            "baskets": len(oos_pnls), "net_pnl": float(sum(oos_pnls)),
            "win_rate_pct": len(wins) / len(oos_pnls) * 100 if oos_pnls else None,
            "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
            "profitable_steps": sum(1 for r in oos_ret if r > 0), "steps": len(oos_ret),
        },
        "avg_in_sample_return_pct": float(np.mean(is_ret)) if is_ret else None,
        "avg_out_of_sample_return_pct": float(np.mean(oos_ret)) if oos_ret else None,
    }


def period_stability(settings: Settings, features: pd.DataFrame, freq: str = "MS", workers=None) -> list[dict]:
    ts = features["timestamp"]
    start = ts.iloc[min(len(ts) - 1, 250)]
    edges = list(pd.date_range(start.normalize(), ts.iloc[-1], freq=freq, tz="UTC"))
    edges = [start] + [e for e in edges if e > start] + [ts.iloc[-1] + pd.Timedelta(seconds=1)]
    windows = [(a, b) for a, b in zip(edges[:-1], edges[1:]) if (b - a) > pd.Timedelta(days=3)]
    res = run_many([_window(settings, a, b) for a, b in windows], features, workers)
    return [{"period": f"{a:%Y-%m-%d} to {b:%Y-%m-%d}", **(pick(r["metrics"]) if "metrics" in r else {"error": r.get("error")})}
            for (a, b), r in zip(windows, res)]
