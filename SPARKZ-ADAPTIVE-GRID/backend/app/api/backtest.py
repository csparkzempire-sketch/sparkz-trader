"""Backtests, the comparison lab and robustness studies."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.common import settings_from
from app.backtest.comparison import compare
from app.backtest.monte_carlo import sensitivity
from app.backtest.runner import prepare_features, run_backtest
from app.backtest.stress_test import run_stress
from app.backtest.studies import robustness_study, walk_forward_study
from app.data.repository import load_candles
from app.models.database import Backtest, session_factory
from app.models.schemas import CompareRequest, RobustnessRequest, RunRequest

router = APIRouter(prefix="/backtest", tags=["backtest"])


def _features(s):
    df = load_candles(s.market.symbol, s.market.timeframe)
    if df.empty:
        raise HTTPException(404, f"No stored data for {s.market.symbol} {s.market.timeframe}. Download it first.")
    return prepare_features(df, s)


@router.post("/run")
def run(req: RunRequest):
    s = settings_from(req.preset, req.overrides)
    try:
        res = run_backtest(s, features=_features(s), save=req.save, name=req.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"id": res.backtest_id, **res.summary()}


@router.get("/list")
def list_runs(limit: int = 50):
    with session_factory()() as ses:
        rows = ses.query(Backtest).order_by(Backtest.id.desc()).limit(limit).all()
        return [{"id": b.id, "name": b.name, "kind": b.kind, "symbol": b.symbol, "timeframe": b.timeframe,
                 "created_at": b.created_at.isoformat(), "start": b.start.isoformat() if b.start else None,
                 "end": b.end.isoformat() if b.end else None, "bars": b.bars,
                 "net_pnl": (b.summary or {}).get("metrics", {}).get("net_pnl"),
                 "baskets": (b.summary or {}).get("metrics", {}).get("baskets"),
                 "max_drawdown_pct": (b.summary or {}).get("metrics", {}).get("max_drawdown_pct")} for b in rows]


@router.get("/{backtest_id}")
def get_run(backtest_id: int):
    with session_factory()() as ses:
        b = ses.get(Backtest, backtest_id)
        if b is None:
            raise HTTPException(404, "no such backtest")
        return {"id": b.id, "name": b.name, **(b.summary or {})}


@router.post("/compare")
def compare_lab(req: CompareRequest):
    try:
        return compare(req.presets, req.shared or None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/stress")
def stress(req: RunRequest):
    s = settings_from(req.preset, req.overrides)
    return run_stress(s, load_candles(s.market.symbol, s.market.timeframe))


@router.post("/robustness")
def robustness(req: RobustnessRequest):
    s = settings_from(req.preset, req.overrides)
    f = _features(s)
    return robustness_study(s, f, req.runs)


@router.post("/sensitivity")
def sensitivity_sweep(req: RunRequest):
    s = settings_from(req.preset, req.overrides)
    return sensitivity(s, _features(s))


@router.post("/walk-forward")
def walk_forward_run(req: RunRequest):
    s = settings_from(req.preset, req.overrides)
    return walk_forward_study(s, _features(s))
