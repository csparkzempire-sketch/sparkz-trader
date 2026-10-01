"""Health, controls (EMERGENCY STOP, resume, close basket, restart with another preset) and the event log."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.state import STATE, runner
from app.backtest.metrics import clean
from app.config import list_presets, load_settings

router = APIRouter(prefix="/api/system", tags=["system"])


class StopBody(BaseModel):
    reason: str = "STOP ROBOT pressed on the dashboard"


class RestartBody(BaseModel):
    preset: str | None = None
    overrides: dict | None = None


@router.get("/health")
def health():
    rn = runner()
    with rn.lock:
        return clean(rn.health())


@router.get("/config")
def config():
    out = []
    for p in list_presets():
        s = load_settings(p, env={})
        out.append({"name": p, "grid": s.grid.mode.value, "sizing": s.sizing.mode.value, "target": s.target.mode.value,
                    "max_positions": s.risk.max_positions, "high_risk": s.high_risk,
                    "video_style_mode": s.video_style_mode})
    r = runner()
    return {"current": r.s.model_dump(mode="json"), "presets": out, "live_trading_enabled": False,
            "environment": r.s.environment.value}


@router.post("/emergency-stop")
def emergency_stop(body: StopBody | None = None):
    rn = runner()
    with rn.lock:
        rn.robot.emergency_stop((body or StopBody()).reason)
    r = rn.robot
    return {"status": r.status, "emergency_stop": r.emergency}


@router.post("/resume")
def resume():
    rn = runner()
    r = rn.robot
    if not r.stopped:
        raise HTTPException(409, "robot is not stopped")
    with rn.lock:
        r.resume()
    return {"status": r.status}


@router.post("/close-basket")
def close_basket():
    rn = runner()
    r = rn.robot
    with rn.lock:
        ok = r.close_basket_manually()
    if not ok:
        raise HTTPException(409, "no open basket (or a close is already pending)")
    return {"status": r.status}


@router.post("/restart")
async def restart(body: RestartBody):
    """Start a NEW paper session (fresh PAPER ACCOUNT) with another preset or settings."""
    if STATE.runner and STATE.runner.robot.strategy.basket is not None and not STATE.runner.robot.stopped:
        raise HTTPException(409, "a basket is open: press STOP ROBOT or close the basket first")
    try:
        load_settings(body.preset, overrides=body.overrides)      # validate before stopping anything
    except Exception as e:
        raise HTTPException(422, str(e))
    await STATE.stop_loop()
    STATE.build(body.preset, body.overrides)
    await STATE.start_loop()
    return {"preset": STATE.settings.name, "run_id": STATE.runner.run_id}


@router.get("/events")
def events(limit: int = 300, types: str | None = None):
    t = set(types.split(",")) if types else None
    rn = runner()
    with rn.lock:
        return clean(rn.robot.log.tail(limit, t))
