"""Market assistant: the latest multi-timeframe, multi-strategy analysis (refreshed every assistant.refresh_seconds)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.state import runner
from app.backtest.metrics import clean

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


@router.get("")
def assistant():
    return clean(runner().assistant_snapshot())
