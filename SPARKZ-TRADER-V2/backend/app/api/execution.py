"""Demo / live results against the backtests (app/engine/live_report.py)."""

from fastapi import APIRouter

from app.api.state import locked, runner
from app.backtest.metrics import clean
from app.engine.live_report import build

router = APIRouter(prefix="/api/execution", tags=["execution"])


@router.get("/report")
@locked
def report():
    return clean(build(runner().robot))
