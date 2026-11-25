"""Demo / live results against the backtests (app/engine/live_report.py), and the Telegram alerts."""

from fastapi import APIRouter, HTTPException

from app.api.state import locked, runner
from app.backtest.metrics import clean
from app.engine.live_report import build

router = APIRouter(prefix="/api/execution", tags=["execution"])


@router.get("/report")
@locked
def report():
    return clean(build(runner().robot))


@router.get("/alerts")
def alerts_status():
    a = runner().alerts
    return a.status() if a else {"enabled": False,
                                 "how": "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before starting the server"}


@router.post("/alerts/test")
def alerts_test():
    a = runner().alerts
    if a is None:
        raise HTTPException(400, "Telegram alerts are off: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    a.push(f"✅ {a.label}: test message. Alerts are working.")
    return {"queued": True}
