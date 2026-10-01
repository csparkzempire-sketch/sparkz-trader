from fastapi import APIRouter

from app.api.state import locked, runner
from app.strategy.analysis import CONFIDENCE_NOTE

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.get("")
@locked
def strategy():
    r = runner().robot
    a, d = r.strategy.last_analysis, r.strategy.last_decision
    return {"name": r.strategy.name, "status": r.status, "analysis": a.to_dict() if a else None,
            "signal": {"action": d.action, "reasons": d.reasons, "confidence": d.confidence} if d else None,
            "confidence_note": CONFIDENCE_NOTE, "cooldown_until_bar": r.strategy.cooldown_until, "bar": r.bar,
            "settings": r.s.model_dump(mode="json"), "high_risk_settings": r.s.high_risk}
