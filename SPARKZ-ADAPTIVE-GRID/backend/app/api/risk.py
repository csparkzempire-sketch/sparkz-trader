"""Risk limits and the current risk status of paper accounts."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import HARD_MAX_POSITIONS, load_config
from app.paper import simulator

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/limits")
def limits():
    s = load_config(env={})
    return {"risk": s.risk.model_dump(), "stop": s.stop.model_dump(), "hard_max_positions": HARD_MAX_POSITIONS,
            "live_trading_enabled": s.live_trading_enabled, "martingale_allowed_by_default": s.sizing.allow_martingale}


@router.get("/status")
def status():
    out = []
    for a in simulator.list_accounts():
        st = simulator.status(a["name"])
        lim = st["limits"]
        out.append({"account": a["name"], "equity": st["equity"], "drawdown_pct": st["drawdown_pct"],
                    "max_drawdown_pct": lim["max_account_drawdown_percent"], "exposure_leverage": st["exposure_leverage"],
                    "max_exposure_leverage": lim["max_exposure_leverage"], "margin_usage_pct": st["margin_usage_pct"],
                    "max_margin_usage_pct": lim["max_margin_usage_percent"],
                    "positions": st["open_basket"]["positions"] if st["open_basket"] else 0,
                    "max_positions": lim["max_positions"], "halted": st["halted"],
                    "emergency": "HALTED" if st["halted"] else "OK"})
    return out
