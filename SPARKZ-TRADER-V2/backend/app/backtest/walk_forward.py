"""
Walk-forward validation and the parameter lab.

Walk-forward (chronological, never shuffled):

  |------ train ------|-- validation --|-- test --|
                      |------ train ------|-- validation --|-- test --|   (rolling)

For each fold:
  1. every candidate in the (small) parameter set is backtested on TRAIN;
  2. the candidates are ranked on VALIDATION (not on train: the best train
     result is the most overfit one), with a minimum number of baskets;
  3. the chosen candidate is run ONCE on TEST, which no decision has seen.
Indicators for every window are computed from earlier data too (warm), but no
window ever trades outside its own dates.

Parameter lab rules (against overfitting):
- at most MAX_CANDIDATES combinations, chosen by a person, not searched;
- every candidate's train AND validation result is reported, not just the winner;
- the report shows how much the winner degrades from train to validation to test,
  and how many candidates were tried (more tries = a luckier-looking winner).
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from app.backtest.engine import run_backtest
from app.backtest.metrics import clean, compute_metrics
from app.config import Settings, deep_merge

MAX_CANDIDATES = 12
MIN_BASKETS = 10
WARMUP_BARS = 600


def _score(m: dict) -> float:
    """Return per unit of drawdown; too few baskets ranks last."""
    if (m["baskets"] or 0) < MIN_BASKETS:
        return -1e9
    dd = abs(m["max_drawdown_pct"] or 0) or 0.1
    return (m["return_pct"] or 0) / dd


def expand(space: dict[str, list]) -> list[dict]:
    """{"grid.atr_multiplier": [0.5, 1.0], "risk.max_positions": [3, 5]} -> list of nested override dicts."""
    keys = list(space)
    combos = list(itertools.product(*(space[k] for k in keys)))
    if len(combos) > MAX_CANDIDATES:
        raise ValueError(f"{len(combos)} combinations requested; the parameter lab allows at most {MAX_CANDIDATES} "
                         "to limit overfitting. Choose fewer values.")
    out = []
    for values in combos:
        ov: dict = {}
        for k, v in zip(keys, values):
            d = ov
            parts = k.split(".")
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = v
        out.append(ov)
    return out


def _flat(ov: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in ov.items():
        if isinstance(v, dict):
            out |= _flat(v, f"{prefix}{k}.")
        else:
            out[f"{prefix}{k}"] = v
    return out


def _metrics(settings: Settings, candles: pd.DataFrame, start: int, end: int) -> dict:
    r = run_backtest(settings, candles, start=start, end=end)
    ro = r.robot
    m = compute_metrics(ro.completed, ro.equity_curve, ro.ledger.fills, settings.risk.initial_capital, ro.inst,
                        settings.market.timeframe, r.candles)
    keep = ("return_pct", "net_pnl", "baskets", "win_rate_pct", "profit_factor", "max_drawdown_pct",
            "largest_basket_loss", "max_positions_in_basket", "start", "end")
    return {k: m[k] for k in keep} | {"buy_and_hold_pct": (m["buy_and_hold"] or {}).get("price_change_pct")}


def _with(settings: Settings, ov: dict) -> Settings:
    return Settings.model_validate(deep_merge(settings.model_dump(mode="json"), ov))


def walk_forward(settings: Settings, candles: pd.DataFrame, space: dict[str, list] | None = None,
                 folds: int = 3, train_frac: float = 0.5, val_frac: float = 0.25, label: str = "") -> dict:
    """Rolling folds over candles[WARMUP_BARS:]. Each fold: train, validation, test (consecutive)."""
    cands = expand(space) if space else [{}]
    n = len(candles)
    usable = n - WARMUP_BARS
    if usable < 400 * folds:
        raise ValueError(f"not enough data: {n} candles for {folds} folds")
    test_frac = 1 - train_frac - val_frac
    # fold length so that the folds' test windows tile the end of the data
    fold_len = int(usable / (1 + (folds - 1) * test_frac))
    step = int(fold_len * test_frac)
    out_folds = []
    for f in range(folds):
        a = WARMUP_BARS + f * step
        tr, va = a + int(fold_len * train_frac), a + int(fold_len * (train_frac + val_frac))
        te = min(a + fold_len, n)
        rows = []
        for ov in cands:
            s = _with(settings, ov)
            m_tr = _metrics(s, candles, a, tr)
            m_va = _metrics(s, candles, tr, va)
            rows.append({"params": _flat(ov), "overrides": ov, "train": m_tr, "validation": m_va,
                         "train_score": _score(m_tr), "validation_score": _score(m_va)})
        best = max(rows, key=lambda r: r["validation_score"])
        best_train = max(rows, key=lambda r: r["train_score"])
        test = _metrics(_with(settings, best["overrides"]), candles, va, te)
        out_folds.append({
            "fold": f + 1,
            "train_period": [str(candles["timestamp"].iloc[a]), str(candles["timestamp"].iloc[tr - 1])],
            "validation_period": [str(candles["timestamp"].iloc[tr]), str(candles["timestamp"].iloc[va - 1])],
            "test_period": [str(candles["timestamp"].iloc[va]), str(candles["timestamp"].iloc[te - 1])],
            "candidates": rows, "chosen": best["params"], "chosen_by_train_would_be": best_train["params"],
            "chosen_train": best["train"], "chosen_validation": best["validation"], "test": test,
        })
    tests = [f["test"] for f in out_folds]
    tr_ret = [f["chosen_train"]["return_pct"] for f in out_folds]
    va_ret = [f["chosen_validation"]["return_pct"] for f in out_folds]
    te_ret = [t["return_pct"] for t in tests]
    summary = {
        "candidates_tried": len(cands), "folds": folds,
        "avg_return_pct": {"train": float(np.mean(tr_ret)), "validation": float(np.mean(va_ret)),
                           "test": float(np.mean(te_ret))},
        "test_folds_profitable": sum(1 for r in te_ret if r > 0),
        "test_worst_drawdown_pct": min(t["max_drawdown_pct"] for t in tests),
        "test_total_baskets": sum(t["baskets"] for t in tests),
        "test_vs_buy_and_hold": [{"strategy_pct": t["return_pct"], "buy_and_hold_pct": t["buy_and_hold_pct"]}
                                 for t in tests],
        "same_choice_every_fold": len({str(f["chosen"]) for f in out_folds}) == 1,
        "note": ("Only the TEST rows are out-of-sample. Train and validation returns are inflated by the choice "
                 f"among {len(cands)} candidate(s)."),
    }
    return clean({"label": label, "preset": settings.name, "folds": out_folds, "summary": summary})


def parameter_lab(settings: Settings, candles: pd.DataFrame, space: dict[str, list], label: str = "") -> dict:
    """One chronological split (60% train / 20% validation / 20% test) over a small, hand-picked parameter set."""
    cands = expand(space)
    n = len(candles)
    a = WARMUP_BARS
    tr, va = a + int((n - a) * 0.6), a + int((n - a) * 0.8)
    rows = []
    for ov in cands:
        s = _with(settings, ov)
        rows.append({"params": _flat(ov), "overrides": ov, "train": _metrics(s, candles, a, tr),
                     "validation": _metrics(s, candles, tr, va)})
    for r in rows:
        r["train_score"], r["validation_score"] = _score(r["train"]), _score(r["validation"])
    rows.sort(key=lambda r: r["validation_score"], reverse=True)
    best = rows[0]
    test = _metrics(_with(settings, best["overrides"]), candles, va, n)
    tr_sorted = sorted(rows, key=lambda r: r["train_score"], reverse=True)
    rank_corr = None
    if len(rows) > 2:
        tr_rank = {id(r): i for i, r in enumerate(tr_sorted)}
        x = [tr_rank[id(r)] for r in rows]
        rank_corr = float(np.corrcoef(x, list(range(len(rows))))[0, 1])
    return clean({
        "label": label, "preset": settings.name, "candidates": rows, "chosen": best["params"],
        "test": test, "candidates_tried": len(cands),
        "train_validation_rank_correlation": rank_corr,
        "warnings": [w for w in [
            "Validation ranking disagrees with train ranking: parameter performance is not stable."
            if rank_corr is not None and rank_corr < 0.3 else None,
            "The chosen parameters lost money on the untouched test period." if test["return_pct"] < 0 else None,
            f"Test has only {test['baskets']} baskets: too few to judge." if test["baskets"] < MIN_BASKETS else None,
        ] if w],
        "split": {"train": [str(candles["timestamp"].iloc[a]), str(candles["timestamp"].iloc[tr - 1])],
                  "validation": [str(candles["timestamp"].iloc[tr]), str(candles["timestamp"].iloc[va - 1])],
                  "test": [str(candles["timestamp"].iloc[va]), str(candles["timestamp"].iloc[n - 1])]},
    })
