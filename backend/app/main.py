"""SPARKZ TRADER FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_backtest, routes_market, routes_models, routes_paper, routes_system
from app.config import settings
from app.database.database import init_db
from app.utils.logging import get_logger

logger = get_logger(__name__, settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("SPARKZ TRADER API starting up. live_trading_enabled=%s", settings.live_trading_enabled)
    yield
    from app.paper.scheduler import paper_feed_scheduler

    paper_feed_scheduler.stop_all()


app = FastAPI(
    title="SPARKZ TRADER API",
    description=(
        "Quantitative research, backtesting, and paper-trading platform. "
        "This is NOT a guaranteed-profit system. Historical performance does "
        "not guarantee future results. Paper trading only — no real-money "
        "trading is enabled by default."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(routes_system.router, tags=["system"])
app.include_router(routes_market.router, prefix="/market", tags=["market"])
app.include_router(routes_backtest.router, prefix="/backtest", tags=["backtest"])
app.include_router(routes_models.router, prefix="/models", tags=["models"])
app.include_router(routes_paper.router, prefix="/paper", tags=["paper"])
