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
    source: str = "yahoo"                # stored data source: yahoo (GC=F future), oanda, mt5 or dukascopy (spot)
    space: dict[str, list] = Field(default_factory=dict)
    folds: int = 3


def _load(b: RunBody):
    from app.market.history import load_history
    from app.market.providers.mock_provider import generate_candles

    s = load_settings(b.preset, overrides=b.overrides)
    if b.data.startswith("synthetic"):
        sc = b.data.split(":", 1)[1] if ":" in b.data else "normal"
        return s, generate_candles(s.market.symbol, s.market.timeframe, b.bars, sc, seed=1), f"synthetic:{sc}"
    return (s, load_history(s.market.symbol, s.market.timeframe, b.source),
            f"stored {b.source} {s.market.symbol} {s.market.timeframe}")


def _backtest(b: RunBody):
    from app.backtest.engine import run_backtest
    from app.backtest.metrics import report
    from app.market.history import load_history

    s, df, label = _load(b)
    path = load_history(s.market.symbol, b.path, b.source) if b.path else None
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


class AssistantBody(BaseModel):
    execution_tf: str = Field(default="1d", pattern="^(1h|4h|1d)$")
    start: str = "2008-01-01"
    end: str | None = None
    spread: float = Field(default=0.24, ge=0, le=5)
    min_confidence: float = Field(default=0.55, ge=0, le=1)
    decision: str = Field(default="VOTE", pattern="^(VOTE|TOP_STRATEGY)$")
    top: int = Field(default=1, ge=1, le=9)
    max_hold_hours: float | None = Field(default=None, gt=0, le=2000)
    max_risk: float | None = Field(default=None, gt=0, le=5000)
    download: bool = True


# the settings of the history studies in reports/TECHNICAL_REPORT.md (sections 21-22), per execution timeframe
ASSISTANT_DEFAULTS = {"1h": (24.0, 200.0), "4h": (120.0, 400.0), "1d": (480.0, 1000.0)}


def gold_1h(start: str, download: bool, progress):
    """Stored Dukascopy hourly gold, topped up from the free feed (cached month files) when allowed."""
    import pandas as pd

    from app.config import DATA_DIR
    from app.market.history import load_history, merge_into_store

    try:
        have = load_history("XAUUSD", "1h", "dukascopy")
    except FileNotFoundError:
        have = None
    want = pd.Timestamp(start, tz="UTC")
    last_month = pd.Timestamp.now(tz="UTC").floor("D") - pd.Timedelta(days=2)
    fresh = have is not None and len(have) and have["timestamp"].iloc[0] <= want + pd.Timedelta(days=7) \
        and have["timestamp"].iloc[-1] >= last_month
    if fresh or not download:
        if have is None or not len(have):
            raise FileNotFoundError("no stored hourly gold history: tick 'download' to fetch it (free, Dukascopy)")
        return have
    from app.market.dukascopy import download_1h

    first = want if have is None or not len(have) or have["timestamp"].iloc[0] > want else \
        have["timestamp"].iloc[-1] - pd.Timedelta(days=31)
    progress(f"downloading hourly gold from {first:%Y-%m} (free Dukascopy feed, cached month by month)")
    df, _ = download_1h("XAUUSD", first.strftime("%Y-%m"), cache_dir=DATA_DIR / "cache" / "dukascopy",
                        progress=lambda i, m: progress(f"downloading hourly gold: {m} ({i} months)"))
    out, _ = merge_into_store(df, "XAUUSD", "1h", "dukascopy")
    return out


def _assistant(b: AssistantBody, progress=lambda m: None):
    import pandas as pd

    from app.assistant.study import run_study

    base = gold_1h(b.start, b.download, progress)
    hold, risk = ASSISTANT_DEFAULTS[b.execution_tf]
    start = max(pd.Timestamp(b.start, tz="UTC"), base["timestamp"].iloc[0] + pd.Timedelta(days=365))   # a year of warm-up
    out = run_study(base, "1h", b.execution_tf, 1, b.spread, b.min_confidence, b.max_hold_hours or hold,
                    b.max_risk or risk, b.decision, b.top, str(start.date()), b.end, "dukascopy", progress, trades_out=200)
    out["meta"]["max_risk"] = b.max_risk or risk
    out["meta"]["symbol"] = "XAUUSD"
    return out


_assistant.with_progress = True
JOBS = {"run": _backtest, "stress": _stress, "walk-forward": _wf, "lab": _lab}


@router.post("/assistant")
def start_assistant(body: AssistantBody):
    """The assistant robot on hourly gold history (the study of the technical report, sections 21-22)."""
    return {"job": STATE.submit("assistant", _assistant, body)}


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
