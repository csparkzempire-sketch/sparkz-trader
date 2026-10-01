"""
Audit log: every decision and action of the robot, in order.

Each entry has the MARKET time it refers to (bar or tick time), the wall-clock
time it was written, a type, a plain-language message and structured data.
Listeners (WebSocket broadcast, database persistence) are called for every entry.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


@dataclass
class LogEntry:
    seq: int
    time: datetime
    wall_time: datetime
    type: str
    message: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"seq": self.seq, "time": self.time.isoformat(), "wall_time": self.wall_time.isoformat(),
                "type": self.type, "message": self.message, "data": self.data}


class EventLog:
    def __init__(self, keep: int | None = 5000):
        self.entries: deque[LogEntry] = deque(maxlen=keep)
        self.seq = 0
        self.counts: dict[str, int] = {}
        self.listeners: list[Callable[[LogEntry], None]] = []

    def add(self, time: datetime, type_: str, message: str, data: dict | None = None) -> LogEntry:
        self.seq += 1
        e = LogEntry(self.seq, time, datetime.now(timezone.utc), type_, message, data or {})
        self.entries.append(e)
        self.counts[type_] = self.counts.get(type_, 0) + 1
        for fn in self.listeners:
            try:
                fn(e)
            except Exception:   # a broken listener must never stop the robot
                pass
        return e

    def tail(self, n: int = 200, types: set[str] | None = None) -> list[dict]:
        rows = [e for e in self.entries if not types or e.type in types]
        return [e.to_dict() for e in rows[-n:]]
