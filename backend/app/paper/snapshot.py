"""
Snapshot feed for the hosted paper-trading status page.

`python -m app.cli paper-snapshot --out-dir DIR` writes two JSON documents the
hourly routine pushes to the page's database:

- snapshot.json: every account's summary (same figures as GET /paper/runs),
  combined totals and the latest prices.
- history.json: combined and per-account equity over time, columnar, for the
  equity chart. Built from data/paper_history.jsonl, which each run appends
  one point to (one per hour; a second run in the same hour replaces it).

Both stay well under the page database's 256 KiB-per-document limit: trade
lists and logs are trimmed and the history keeps the last HISTORY_DAYS days.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from app.api.schemas import PaperRunSummary
from app.paper.runner import PAPER_DIR

HISTORY_PATH = PAPER_DIR.parent / "paper_history.jsonl"
HISTORY_DAYS = 90
MAX_TRADES = 40
MAX_LOG = 20


def build_snapshot(summaries: list[PaperRunSummary], now: datetime) -> dict:
    accounts = []
    for s in summaries:
        d = s.model_dump(mode="json")
        d["closed_trade_count"] = len(d["closed_trades"])
        d["closed_trades"] = d["closed_trades"][:MAX_TRADES]
        d["log"] = d["log"][:MAX_LOG]
        accounts.append(d)
    start = sum(s.starting_balance for s in summaries)
    equity = sum(s.equity for s in summaries)
    prices = {}
    for s in summaries:
        if s.symbol not in prices:
            prices[s.symbol] = {
                "price": s.latest_price,
                "at": None if s.latest_price_at is None else s.latest_price_at.isoformat(),
                "error": s.price_error,
            }
    return {
        "updated_at": now.isoformat(timespec="seconds"),
        "totals": {
            "accounts": len(summaries),
            "starting_balance": start,
            "equity": round(equity, 2),
            "return_pct": round((equity / start - 1) * 100, 4) if start else 0.0,
            "open_positions": sum(len(s.open_positions) for s in summaries),
            "closed_trades": sum(len(s.closed_trades) for s in summaries),
            "winning_trades": sum(s.winning_trades for s in summaries),
            "halted": sum(1 for s in summaries if s.halted),
        },
        "prices": prices,
        "accounts": accounts,
    }


def append_history(summaries: list[PaperRunSummary], now: datetime, path: Path = HISTORY_PATH) -> None:
    hour = now.replace(minute=0, second=0, microsecond=0).isoformat()
    point = {
        "t": hour,
        "total": round(sum(s.equity for s in summaries), 2),
        "accounts": {s.account_name: round(s.equity, 2) for s in summaries},
    }
    lines = path.read_text().splitlines() if path.exists() else []
    if lines and json.loads(lines[-1])["t"] == hour:
        lines[-1] = json.dumps(point)
    else:
        lines.append(json.dumps(point))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def history_doc(path: Path = HISTORY_PATH, now: datetime | None = None, days: int = HISTORY_DAYS) -> dict:
    points = [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
    if now is not None:
        cutoff = (now - timedelta(days=days)).isoformat()
        points = [p for p in points if p["t"] >= cutoff]
    names = sorted({n for p in points for n in p["accounts"]})
    return {
        "t": [p["t"] for p in points],
        "total": [p["total"] for p in points],
        "accounts": {n: [p["accounts"].get(n) for p in points] for n in names},
    }
