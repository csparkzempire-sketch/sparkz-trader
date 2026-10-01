from fastapi import APIRouter

from app.api.state import locked, runner

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("")
@locked
def market():
    r = runner()
    lt = r.robot.market.live or r.robot.market.last_bar
    return {"symbol": r.s.market.symbol, "timeframe": r.s.market.timeframe, "state": lt.to_dict() if lt else None,
            "provider": {"name": r.info.name, "live": r.info.live, "delayed": r.info.delayed, "notes": r.info.notes},
            "market_open": r.market_open, "data_ok": r.robot.data_ok, "data_reason": r.robot.data_reason}


@router.get("/candles")
@locked
def candles(n: int = 200):
    return runner()._candles(n)
