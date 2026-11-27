"""Trade approvals: proposals from the assistant on every watched market; nothing trades until you approve."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.state import STATE
from app.approvals.manager import ApprovalError
from app.backtest.metrics import clean

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


def _m():
    if STATE.approvals is None:
        raise HTTPException(503, "trade approvals are not running (APPROVALS=off?)")
    return STATE.approvals


@router.get("")
def approvals():
    m = _m()
    out = m.snapshot()
    tg = STATE.approval_bot
    out["telegram"] = None if tg is None else {"buttons": tg.buttons, "errors": tg.errors, "last_error": tg.last_error}
    return clean(out)


def _act(fn, pid: int):
    try:
        return clean(fn(pid, via="dashboard"))
    except ApprovalError as e:
        raise HTTPException(409, str(e))


@router.post("/{pid}/approve")
def approve(pid: int):
    return _act(_m().approve, pid)


@router.post("/{pid}/reject")
def reject(pid: int):
    return _act(_m().reject, pid)


@router.post("/{pid}/close")
def close(pid: int):
    return _act(_m().close, pid)


@router.post("/scan")
def scan():
    """Look at every watched market now (normally done every 30 s, on each candle close)."""
    return clean({"new": _m().scan()})
