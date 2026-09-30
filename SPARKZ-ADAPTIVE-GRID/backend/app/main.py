"""
SPARKZ Adaptive Grid & Basket - research API.

Backtesting and paper trading only. Startup fails if the configuration asks
for live trading (see app.config); there is no broker execution code.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import backtest, baskets, market, paper, risk, strategy
from app.config import load_config

SETTINGS = load_config()   # raises at import if LIVE_TRADING is requested: the API won't start

app = FastAPI(title="SPARKZ Adaptive Grid & Basket", version="1.0",
              description="Quantitative research and paper simulation. No live trading.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                                                   "http://localhost:4174"], allow_methods=["*"], allow_headers=["*"])
for r in (market.router, strategy.router, backtest.router, baskets.router, paper.router, risk.router):
    app.include_router(r)


@app.get("/health")
def health():
    return {"status": "ok", "mode": "paper/backtest only", "live_trading_enabled": SETTINGS.live_trading_enabled}
