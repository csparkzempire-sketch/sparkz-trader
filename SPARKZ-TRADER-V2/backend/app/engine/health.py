"""
SYSTEM HEALTH: is the data fresh, is the robot running, is anything failing?

Overall state:
  OK        data fresh, strategy running, no recent errors
  DEGRADED  running, but with recent errors, a closed market or delayed data
  STALE     market data stale: new entries are blocked
  STOPPED   emergency stop or drawdown halt active
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


def process_stats() -> dict:
    if psutil is None:  # pragma: no cover
        return {"cpu_percent": None, "memory_mb": None, "system_memory_percent": None}
    p = psutil.Process(os.getpid())
    return {"cpu_percent": p.cpu_percent(interval=None), "memory_mb": p.memory_info().rss / 1e6,
            "system_memory_percent": psutil.virtual_memory().percent}


def build_health(robot, provider_info=None, market_open: bool | None = None, last_tick_at: datetime | None = None,
                 data_age_s: float | None = None, stale_after_s: float | None = None, errors: int = 0,
                 last_error: str | None = None, db=None, started_at: float | None = None,
                 loop_running: bool = True) -> dict:
    db_ok = db.ping() if db is not None else None
    if robot.stopped:
        overall = "STOPPED"
    elif not robot.data_ok:
        overall = "STALE"
    elif errors or (db is not None and db.errors) or market_open is False or (provider_info and provider_info.delayed):
        overall = "DEGRADED"
    else:
        overall = "OK"
    return {
        "overall": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "provider": provider_info.name if provider_info else None,
            "provider_kind": provider_info.kind if provider_info else None,
            "live_prices": provider_info.live if provider_info else None,
            "delayed": provider_info.delayed if provider_info else None,
            "notes": provider_info.notes if provider_info else [],
            "connected": robot.data_ok and errors == 0,
            "fresh": robot.data_ok,
            "reason": robot.data_reason,
            "last_tick_at": last_tick_at.isoformat() if last_tick_at else None,
            "data_age_seconds": data_age_s,
            "stale_after_seconds": stale_after_s,
            "market_open": market_open,
        },
        "strategy": {"name": robot.strategy.name, "status": robot.status, "running": loop_running and not robot.stopped,
                     "emergency_stop": robot.emergency, "halted": robot.halted, "bars_processed": robot.bar},
        "executor": {"kind": "SIMULATION", "live_trading_enabled": False,
                     "pending_orders": len(robot.executor.pending())},
        "database": {"connected": db_ok, "write_errors": db.errors if db else None,
                     "last_error": db.last_error if db else None},
        "process": {**process_stats(), "uptime_seconds": time.time() - started_at if started_at else None},
        "errors": {"count": errors, "last": last_error},
    }
