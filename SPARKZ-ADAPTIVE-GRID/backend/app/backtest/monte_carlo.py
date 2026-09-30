"""
Robustness: how much of a backtest result is luck or a lucky assumption?

1. Sequence tests on the finished baskets (no re-simulation):
   - permutation: same baskets, random order -> distribution of max drawdown
     (the final P&L can't change, the path can);
   - bootstrap: baskets drawn with replacement -> distribution of final return,
     probability of ending in loss, of hitting the account drawdown limit.
2. Perturbation reruns (full re-simulation, in worker processes): each run
   draws random execution and parameter changes:
     spread x U(0.8, 2.0), slippage x U(0.5, 3.0), entry latency 0-2 bars,
     grid spacing, basket target and basket loss limit each x U(0.8, 1.2).
   The spread of results shows how fragile the configuration is.
3. Sensitivity sweeps: one parameter at a time (grid spacing, sizing mode,
   max positions, target, spread), everything else fixed.

A single backtest is one draw; these distributions are the honest summary.
"""

from __future__ import annotations

import numpy as np

from app.backtest.parallel import run_many
from app.backtest.summary import pick
from app.config import GridMode, Settings, SizingMode, TargetMode


def _dd(eq: np.ndarray, capital: float) -> float:
    path = np.concatenate([[capital], eq])
    peak = np.maximum.accumulate(path)
    return float(((path / peak) - 1).min() * 100)


def _pct(a, qs=(5, 25, 50, 75, 95)) -> dict:
    a = np.asarray(a, dtype=float)
    return {f"p{q}": float(np.percentile(a, q)) for q in qs} | {"min": float(a.min()), "max": float(a.max())} if len(a) else {}


def sequence_tests(pnls: list[float], capital: float, dd_limit_pct: float, n: int = 2000, seed: int = 1) -> dict:
    if len(pnls) < 2:
        return {"note": "fewer than 2 baskets: nothing to resample"}
    rng = np.random.default_rng(seed)
    p = np.asarray(pnls, dtype=float)
    perm_dd = [_dd(capital + np.cumsum(rng.permutation(p)), capital) for _ in range(n)]
    boot = rng.choice(p, size=(n, len(p)), replace=True)
    boot_ret = boot.sum(axis=1) / capital * 100
    boot_dd = [_dd(capital + np.cumsum(row), capital) for row in boot]
    return {
        "baskets": len(p), "runs": n,
        "actual_max_drawdown_pct": _dd(capital + np.cumsum(p), capital),
        "permutation_max_drawdown_pct": _pct(perm_dd),
        "bootstrap_return_pct": _pct(boot_ret),
        "bootstrap_max_drawdown_pct": _pct(boot_dd),
        "prob_loss_pct": float((boot_ret < 0).mean() * 100),
        "prob_hit_drawdown_limit_pct": float((np.asarray(boot_dd) <= -dd_limit_pct).mean() * 100),
    }


def _scaled(s: Settings, rng) -> Settings:
    ex = s.execution.model_copy(update={
        "spread_multiplier": s.execution.spread_multiplier * rng.uniform(0.8, 2.0),
        "slippage_multiplier": s.execution.slippage_multiplier * rng.uniform(0.5, 3.0),
        "entry_latency_bars": int(rng.integers(0, 3)),
    })
    g = s.grid.model_copy(update={"atr_multiplier": s.grid.atr_multiplier * rng.uniform(0.8, 1.2),
                                  "price_step": s.grid.price_step * rng.uniform(0.8, 1.2)})
    t = s.target.model_copy(update={k: getattr(s.target, k) * rng.uniform(0.8, 1.2)
                                    for k in ("fixed_usd", "percent", "risk_reward", "atr_multiplier")})
    k = rng.uniform(0.8, 1.2)   # the loss limit is the tighter of these two, so scale both
    st = s.stop.model_copy(update={"max_basket_loss_percent": min(100.0, s.stop.max_basket_loss_percent * k)})
    rk = s.risk.model_copy(update={"risk_per_cycle": min(1.0, s.risk.risk_per_cycle * k)})
    return s.model_copy(update={"execution": ex, "grid": g, "target": t, "stop": st, "risk": rk})


def perturbation_runs(settings: Settings, features, n: int = 40, seed: int = 2, workers=None) -> dict:
    rng = np.random.default_rng(seed)
    variants = [_scaled(settings, rng) for _ in range(n)]
    res = [r for r in run_many(variants, features, workers) if "metrics" in r]
    rets = [r["metrics"]["return_pct"] for r in res]
    dds = [r["metrics"]["max_drawdown_pct"] for r in res]
    pfs = [r["metrics"]["profit_factor"] for r in res if isinstance(r["metrics"]["profit_factor"], (int, float))]
    return {
        "runs": len(res),
        "return_pct": _pct(rets), "max_drawdown_pct": _pct(dds), "profit_factor": _pct(pfs),
        "profitable_runs_pct": float(np.mean([x > 0 for x in rets]) * 100) if rets else None,
        "halted_runs_pct": float(np.mean([bool(r["metrics"]["halted"]) for r in res]) * 100) if res else None,
    }


def sensitivity(settings: Settings, features, workers=None) -> dict:
    """One parameter at a time. Each table row: the value, then the key metrics."""
    sweeps: dict[str, list[tuple[str, Settings]]] = {}

    def upd(section: str, **kw) -> Settings:
        return settings.model_copy(update={section: getattr(settings, section).model_copy(update=kw)})

    if settings.grid.mode == GridMode.PRICE:
        base = settings.grid.price_step
        sweeps["grid_spacing"] = [(f"{v:g} price units", upd("grid", price_step=v)) for v in
                                  [base * k for k in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)]]
    else:
        sweeps["grid_spacing"] = [(f"ATR x {v:g}", upd("grid", atr_multiplier=v))
                                  for v in (0.15, 0.25, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0)]
    sweeps["position_sizing"] = [
        (m.value + (" (HIGH RISK)" if m == SizingMode.MARTINGALE else ""),
         upd("sizing", mode=m, allow_martingale=(m == SizingMode.MARTINGALE) or settings.sizing.allow_martingale))
        for m in SizingMode
    ]
    sweeps["max_positions"] = [(str(v), upd("risk", max_positions=v)) for v in (1, 2, 3, 5, 8, 12)]
    sweeps["basket_target_percent"] = [(f"{v:g}%", upd("target", mode=TargetMode.PERCENT, percent=v)) for v in (0.1, 0.25, 0.5, 1.0, 2.0)]
    sweeps["basket_loss_limit_percent"] = [
        (f"{v:g}%", upd("stop", max_basket_loss_percent=v).model_copy(
            update={"risk": settings.risk.model_copy(update={"risk_per_cycle": v / 100})}))
        for v in (0.5, 1.0, 2.0, 3.0, 5.0)]
    sweeps["spread_multiplier"] = [(f"x{v:g}", upd("execution", spread_multiplier=v)) for v in (0.0, 0.5, 1.0, 2.0, 3.0)]
    flat = [(name, label, s) for name, items in sweeps.items() for label, s in items]
    results = run_many([s for _, _, s in flat], features, workers)
    out: dict[str, list[dict]] = {}
    for (name, label, _), r in zip(flat, results):
        out.setdefault(name, []).append({"value": label, **(pick(r["metrics"]) if "metrics" in r else {"error": r.get("error")})})
    return out
