"""
SPARKZ TRADER V2 API.

  uvicorn app.main:app --port 8000          (from backend/)

Starts the paper runner with the configured provider (MARKET_DATA_PROVIDER,
default MOCK) and serves the dashboard API plus a live WebSocket. Orders are
simulated, unless EXECUTION_MODE=MT5_DEMO or MT5_LIVE sends them to the user's MT5 account through the bridge (app/execution/mt5_executor.py).
"""

from __future__ import annotations

import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import account, assistant, backtest, baskets, bridge, chart, execution, market, strategy, system, trades
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
def dashboard_key_ok(key: str | None) -> bool:
    """SPARKZ_DASHBOARD_PASSWORD set (an online server): every dashboard request must carry it. Unset (on your
    own PC): no password. The bridge endpoints use their own SPARKZ_BRIDGE_TOKEN."""
    expected = os.getenv("SPARKZ_DASHBOARD_PASSWORD", "")
    return not expected or bool(key) and hmac.compare_digest(key.encode(), expected.encode())


async def _dashboard_guard(request, call_next):
    p = request.url.path
    if (request.method != "OPTIONS" and p.startswith("/api/") and not p.startswith("/api/bridge/")
            and not dashboard_key_ok(request.headers.get("x-dashboard-key"))):
        return JSONResponse({"detail": "dashboard password required"}, status_code=401)
    return await call_next(request)


app.add_middleware(BaseHTTPMiddleware, dispatch=_dashboard_guard)       # inside CORS: a 401 still gets CORS headers
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
                   allow_methods=["*"], allow_headers=["*"])
for r in (market.router, strategy.router, baskets.router, account.router, backtest.router, system.router,
          bridge.router, assistant.router, execution.router, chart.router, trades.router):
    app.include_router(r)


@app.get("/api/dashboard")
def dashboard(events: int = 150):
    from app.api.state import runner
    return runner().dashboard(events)


@app.websocket("/ws/live")
async def live(ws: WebSocket):
    """Pushes the full dashboard state whenever something changed (checked every second)."""
    if not dashboard_key_ok(ws.query_params.get("key")):
        await ws.close(code=4401)
        return
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
