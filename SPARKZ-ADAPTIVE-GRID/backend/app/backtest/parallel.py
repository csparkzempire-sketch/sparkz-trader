"""Run many backtests over the same precomputed features, in worker processes."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

_FEATURES: pd.DataFrame | None = None


def _init(features: pd.DataFrame) -> None:
    global _FEATURES
    _FEATURES = features


def _one(settings_json: dict) -> dict:
    from app.backtest.runner import run_backtest
    from app.config import Settings

    s = Settings.model_validate(settings_json)
    try:
        r = run_backtest(s, features=_FEATURES)
    except ValueError as exc:
        return {"error": str(exc)}
    from app.backtest.metrics import downsample_curve

    return {"metrics": dict(r.metrics), "basket_pnls": [b.pnl for b in r.baskets],
            "curve": downsample_curve(r.curve, 400)}


def run_many(settings_list, features: pd.DataFrame, workers: int | None = None) -> list[dict]:
    payload = [s.model_dump(mode="json") for s in settings_list]
    workers = workers or min(len(payload), max(1, (os.cpu_count() or 2) - 1))
    if workers <= 1 or len(payload) == 1:
        _init(features)
        return [_one(p) for p in payload]
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(features,)) as pool:
        return list(pool.map(_one, payload))
