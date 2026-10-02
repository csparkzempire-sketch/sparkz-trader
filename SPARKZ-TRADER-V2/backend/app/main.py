"""
SPARKZ TRADER V2 API.

  uvicorn app.main:app --port 8000          (from backend/)

Starts the paper runner with the configured provider (MARKET_DATA_PROVIDER,
default MOCK) and serves the dashboard API plus a live WebSocket. Everything
is simulated: there is no order route to any broker.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import account, backtest, baskets, bridge, market, strategy, system
from app.api.state import STATE
from app.config import load_env_file
from app.utils.logging import setup as log_setup

load_env_file()
log_setup(os.getenv("LOG_LEVEL", "INFO"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("SPARKZ_NO_PAPER") != "1":
        STATE.build()
        await STATE.start_loop()
    yield
    await STATE.stop_loop()


app = FastAPI(title="SPARKZ TRADER V2", description="Market data + adaptive grid & basket SIMULATOR. "
              "No live trading.", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
                   allow_methods=["*"], allow_headers=["*"])
for r in (market.router, strategy.router, baskets.router, account.router, backtest.router, system.router,
          bridge.router):
    app.include_router(r)


@app.get("/api/dashboard")
def dashboard(events: int = 150):
    from app.api.state import runner
    return runner().dashboard(events)


@app.websocket("/ws/live")
async def live(ws: WebSocket):
    """Pushes the full dashboard state whenever something changed (checked every second)."""
    await ws.accept()
    last = None
    try:
        while True:
            r = STATE.runner
            if r is not None:
                lt = r.last_tick
                key = (id(r), r.robot.log.seq, lt.time if lt else None, lt.bid if lt else None, r.robot.status)
                if key != last:
                    last = key
                    await ws.send_json(r.dashboard(150))
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, RuntimeError):
        return


DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")
