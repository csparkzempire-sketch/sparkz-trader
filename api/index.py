"""
Vercel entry point: serves the FastAPI backend (backend/app) under /api, next to the
dashboard's static build.

A Vercel function can only write to /tmp, and /tmp is wiped whenever the function
goes cold. So the market-data cache, trained models, reports and the SQLite
database all live under /tmp/sparkz: fine for a demo, but nothing written through
the API is kept.

The paper accounts live on the repo's paper-trading branch, which the hourly paper
run updates. Whatever branch this deployment was built from, /api/paper requests
fetch the latest copies from GitHub (at most every SYNC_SECONDS), so the dashboard
shows live accounts without a redeploy. SPARKZ_PAPER_REPO / SPARKZ_PAPER_BRANCH
change the source; SPARKZ_PAPER_REPO="" turns the sync off, leaving whatever
data/paper the deployment was built with.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.request
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

PAPER_REPO = os.getenv("SPARKZ_PAPER_REPO", "csparkzempire-sketch/sparkz-trader")
PAPER_BRANCH = os.getenv("SPARKZ_PAPER_BRANCH", "paper-trading")
SYNC_SECONDS = 300
_sync_lock = threading.Lock()
_synced_at = 0.0


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "sparkz-trader-vercel"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read()


def _save(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(_fetch(url))
    tmp.replace(dest)


def sync_paper_accounts() -> None:
    """Refresh data/paper and paper_history.jsonl from the paper-trading branch on GitHub.
    Best effort: on any failure the copies already here are kept."""
    global _synced_at
    if not PAPER_REPO or time.time() - _synced_at < SYNC_SECONDS or not _sync_lock.acquire(blocking=False):
        return
    try:
        _synced_at = time.time()
        paper = data / "paper"
        paper.mkdir(exist_ok=True)
        try:
            listing = json.loads(_fetch(f"https://api.github.com/repos/{PAPER_REPO}/contents/data/paper?ref={PAPER_BRANCH}"))
            names = [e["name"] for e in listing if e.get("type") == "file"]
        except Exception:  # e.g. GitHub's unauthenticated rate limit: refresh the accounts already known
            names = [p.name for p in paper.glob("*.json")]
        raw = f"https://raw.githubusercontent.com/{PAPER_REPO}/{PAPER_BRANCH}/data"
        for name in names:
            if re.fullmatch(r"[A-Za-z0-9_.-]+\.json", name):
                try:
                    _save(f"{raw}/paper/{name}", paper / name)
                except Exception:
                    pass
        try:
            _save(f"{raw}/paper_history.jsonl", data / "paper_history.jsonl")
        except Exception:
            pass
    finally:
        _sync_lock.release()


sys.path.insert(0, str(ROOT / "backend"))

from fastapi import FastAPI, Request  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

from app.database.database import init_db  # noqa: E402
from app.main import app as backend  # noqa: E402

init_db()  # a mounted app's lifespan doesn't run, so create the tables here

# The dashboard calls /api/<route>; locally Vite strips /api before proxying to uvicorn.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/api", backend)


@app.middleware("http")
async def _fresh_paper_accounts(request: Request, call_next):
    if request.url.path.startswith("/api/paper"):
        await run_in_threadpool(sync_paper_accounts)
    return await call_next(request)
