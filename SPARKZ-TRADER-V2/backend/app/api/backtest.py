"""Research endpoints: backtest, stress tests, walk-forward, parameter lab (run as background jobs)."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.state import STATE
from app.config import REPORTS_DIR, load_settings

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class RunBody(BaseModel):
    preset: str | None = None
    overrides: dict | None = None
    data: str = "stored"                 # stored | synthetic:<scenario>
    bars: int = 3000
    path: str | None = None              # finer stored timeframe for the intrabar path, e.g. "5m"
    space: dict[str, list] = Field(default_factory=dict)
    folds: int = 3


def _load(b: RunBody):
    from app.market.history import load_history
    from app.market.providers.mock_provider import generate_candles

    s = load_settings(b.preset, overrides=b.overrides)
    if b.data.startswith("synthetic"):
        sc = b.data.split(":", 1)[1] if ":" in b.data else "normal"
        return s, generate_candles(s.market.symbol, s.market.timeframe, b.bars, sc, seed=1), f"synthetic:{sc}"
    return s, load_history(s.market.symbol, s.market.timeframe), f"stored {s.market.symbol} {s.market.timeframe}"


def _backtest(b: RunBody):
    from app.backtest.engine import run_backtest
    from app.backtest.metrics import report
    from app.market.history import load_history

    s, df, label = _load(b)
    path = load_history(s.market.symbol, b.path) if b.path else None
    out = report(run_backtest(s, df, label, path_candles=path))
    out["data_label"] = label + (f" (intrabar path from {b.path} candles where available)" if b.path else "")
    return out


def _stress(b: RunBody):
    from app.backtest.stress_test import run_all
    s, df, label = _load(b)
    return run_all(s, None if b.data.startswith("synthetic") else df, label)


def _wf(b: RunBody):
    from app.backtest.walk_forward import walk_forward
    s, df, label = _load(b)
    return walk_forward(s, df, b.space or None, folds=b.folds, label=label)


def _lab(b: RunBody):
    from app.backtest.walk_forward import parameter_lab
    s, df, label = _load(b)
    return parameter_lab(s, df, b.space, label=label)


JOBS = {"run": _backtest, "stress": _stress, "walk-forward": _wf, "lab": _lab}


@router.post("/{kind}")
def start(kind: str, body: RunBody):
    if kind not in JOBS:
        raise HTTPException(404, kind)
    if kind == "lab" and not body.space:
        raise HTTPException(422, "the parameter lab needs a parameter space")
    try:
        load_settings(body.preset, overrides=body.overrides)
    except Exception as e:
        raise HTTPException(422, str(e))
    return {"job": STATE.submit(kind, JOBS[kind], body)}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = STATE.jobs.get(jid)
    if j is None:
        raise HTTPException(404, "unknown job")
    return j


@router.get("/reports")
def reports():
    out = []
    for p in sorted(REPORTS_DIR.glob("*/*.json"), reverse=True)[:100]:
        out.append({"kind": p.parent.name, "name": p.stem, "size": p.stat().st_size})
    return out


@router.get("/reports/{kind}/{name}")
def report_file(kind: str, name: str):
    p = (REPORTS_DIR / kind / f"{name}.json").resolve()
    if REPORTS_DIR.resolve() not in p.parents or not p.exists():
        raise HTTPException(404, "no such report")
    return json.loads(p.read_text())
