from __future__ import annotations

from fastapi import APIRouter

from app.config import settings

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
