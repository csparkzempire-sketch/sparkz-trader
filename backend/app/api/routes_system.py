from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import InstrumentOut
from app.config import settings
from app.markets.instruments import INSTRUMENTS

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "live_trading_enabled": settings.live_trading_enabled,
        "disclaimer": "Paper trading / research platform only. Historical performance does not guarantee future results.",
    }


@router.get("/instruments", response_model=list[InstrumentOut])
def list_instruments() -> list[InstrumentOut]:
    """Markets with a known cost profile. Other symbols fall back to the global PIP_SIZE/SPREAD_PIPS settings."""
    return [
        InstrumentOut(
            symbol=i.symbol,
            display_name=i.display_name,
            asset_class=i.asset_class,
            pip_size=i.pip_size,
            spread_pips=i.spread_pips,
            slippage_pips=i.slippage_pips,
            trades_24_7=i.trades_24_7,
        )
        for i in INSTRUMENTS.values()
    ]
