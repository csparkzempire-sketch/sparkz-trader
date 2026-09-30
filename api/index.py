"""
Vercel entry point: serves the FastAPI backend (backend/app) under /api, next to the
dashboard's static build.

A Vercel function can only write to /tmp, and /tmp is wiped whenever the function
goes cold. So the market-data cache, trained models, reports and the SQLite
database all live under /tmp/sparkz: fine for a demo, but nothing written through
the API is kept. The paper accounts in data/paper (committed on the paper-trading
branch) are copied there on start-up so the dashboard can show them.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path("/tmp/sparkz")

os.environ.setdefault("SPARKZ_DATA_DIR", str(TMP / "data"))
os.environ.setdefault("SPARKZ_MODELS_DIR", str(TMP / "models"))
os.environ.setdefault("SPARKZ_REPORTS_DIR", str(TMP / "reports"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TMP}/sparkz_trader.db")
os.environ.setdefault("XDG_CACHE_HOME", str(TMP / "cache"))  # yfinance's timezone cache

data = Path(os.environ["SPARKZ_DATA_DIR"])
data.mkdir(parents=True, exist_ok=True)
if (ROOT / "data" / "paper").is_dir() and not (data / "paper").exists():
    shutil.copytree(ROOT / "data" / "paper", data / "paper")
if (ROOT / "data" / "paper_history.jsonl").is_file() and not (data / "paper_history.jsonl").exists():
    shutil.copy(ROOT / "data" / "paper_history.jsonl", data / "paper_history.jsonl")

sys.path.insert(0, str(ROOT / "backend"))

from fastapi import FastAPI  # noqa: E402

from app.database.database import init_db  # noqa: E402
from app.main import app as backend  # noqa: E402

init_db()  # a mounted app's lifespan doesn't run, so create the tables here

# The dashboard calls /api/<route>; locally Vite strips /api before proxying to uvicorn.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/api", backend)
