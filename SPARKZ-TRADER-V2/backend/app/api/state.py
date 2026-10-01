"""Process-wide state for the API: the paper runner, its loop task, background research jobs."""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from datetime import datetime, timezone

from app.config import Settings, load_settings
from app.database.repository import Repository
from app.market.data_provider import make_provider
from app.paper.runner import PaperRunner


class AppState:
    def __init__(self):
        self.repo: Repository | None = None
        self.runner: PaperRunner | None = None
        self.task: asyncio.Task | None = None
        self.settings: Settings | None = None
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def build(self, preset: str | None = None, overrides: dict | None = None) -> None:
        self.settings = load_settings(preset or os.getenv("SPARKZ_PRESET") or None, overrides=overrides)
        if self.repo is None:
            self.repo = Repository(os.getenv("SPARKZ_V2_DB_URL") or None)
        self.runner = PaperRunner(self.settings, make_provider(self.settings), self.repo)
        self.runner.start()

    async def start_loop(self) -> None:
        if self.runner and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.runner.run())

    async def stop_loop(self) -> None:
        if self.runner:
            self.runner.stop_loop()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=30)
            except Exception:
                self.task.cancel()
        self.task = None

    # ------------------------------------------------------------------ research jobs
    def submit(self, kind: str, fn, *args) -> str:
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "kind": kind, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
               "result": None, "error": None}
        self.jobs[jid] = job

        def work():
            try:
                job["result"] = fn(*args)
                job["status"] = "done"
            except Exception as e:  # reported to the UI, never swallowed
                job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
        threading.Thread(target=work, daemon=True).start()
        if len(self.jobs) > 50:
            for k in list(self.jobs)[:-50]:
                self.jobs.pop(k, None)
        return jid


STATE = AppState()


def runner() -> PaperRunner:
    if STATE.runner is None:
        from fastapi import HTTPException
        raise HTTPException(503, "paper runner not started")
    return STATE.runner


def locked(fn):
    """Run an endpoint under the paper runner's lock and make the result JSON-safe."""
    import functools

    from app.backtest.metrics import clean

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        rn = STATE.runner
        if rn is None:
            return clean(fn(*a, **kw))
        with rn.lock:
            return clean(fn(*a, **kw))
    return wrapper
