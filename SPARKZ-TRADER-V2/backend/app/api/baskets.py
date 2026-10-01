from fastapi import APIRouter

from app.api.state import STATE, locked, runner

router = APIRouter(prefix="/api/baskets", tags=["baskets"])


@router.get("/current")
@locked
def current():
    snap = runner().robot.snapshot()
    return {"basket": snap["basket"], "positions": snap["positions"], "pending_orders": snap["pending_orders"]}


@router.get("/history")
@locked
def history(limit: int = 200):
    r = runner()
    if STATE.repo is not None and r.run_id is not None:
        return STATE.repo.baskets(r.run_id, limit)
    return [b.to_dict() for b in r.robot.completed[-limit:]]
