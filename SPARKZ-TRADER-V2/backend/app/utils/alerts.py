"""
Telegram alerts: the robot's important events sent to your phone.

  TELEGRAM_BOT_TOKEN=<from @BotFather>      never commit it, never paste it in a chat
  TELEGRAM_CHAT_ID=<your chat id>           (send any message to your bot, then open
                                             https://api.telegram.org/bot<TOKEN>/getUpdates and read chat.id)
  TELEGRAM_ALERTS=POSITION_OPENED,...       optional: which event types (default: ALERT_TYPES below)

What is sent: trades opened, added and closed (with the result), orders MT5 refused (including the daily loss
stop), a basket MT5 closed by itself, emergency stop / drawdown halt / resume, stale or lost market data (the
bridge or MT5 stopped) and its recovery, warnings, the start of the robot, and new BOS / CHoCH on watched
markets (app/assistant/structure_alerts.py).

Sending never slows or stops the robot: messages go through a queue and a background thread; a failed send is
counted and dropped. Repeated messages of the same type are limited to one per `repeat_seconds` (trades are
never limited). The token is used only in the request URL and never logged.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from typing import Callable

ALERT_TYPES = ("STARTED", "POSITION_OPENED", "POSITION_ADDED", "BASKET_CLOSED", "BROKER_CLOSED", "ORDER_REJECTED",
               "EMERGENCY_STOP", "HALT", "RESUMED", "DATA_STALE", "DATA_OK", "WARNING", "ADOPTED", "ACCOUNT_SYNCED",
               "STRUCTURE")
NEVER_LIMITED = {"POSITION_OPENED", "POSITION_ADDED", "BASKET_CLOSED", "BROKER_CLOSED", "EMERGENCY_STOP", "HALT",
                 "STRUCTURE"}
ICONS = {"POSITION_OPENED": "🟢", "POSITION_ADDED": "➕", "BASKET_CLOSED": "🏁", "BROKER_CLOSED": "⚠️",
         "ORDER_REJECTED": "⛔", "EMERGENCY_STOP": "🛑", "HALT": "🛑", "RESUMED": "▶️", "DATA_STALE": "📡",
         "DATA_OK": "📡", "WARNING": "⚠️", "STARTED": "🤖", "ADOPTED": "🔄", "ACCOUNT_SYNCED": "🔄",
         "STRUCTURE": "📐"}


def telegram_sender(token: str, chat_id: str, timeout: float = 10.0) -> Callable[[str], None]:
    def send(text: str) -> None:
        import httpx

        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=timeout)
        if r.status_code != 200:
            raise RuntimeError(f"Telegram answered HTTP {r.status_code}")    # never echo the URL (it has the token)
    return send


class Alerts:
    def __init__(self, send: Callable[[str], None], label: str = "SPARKZ", types=ALERT_TYPES,
                 repeat_seconds: float = 300.0, clock=time.time, background: bool = True):
        self.send, self.label, self.types = send, label, set(types)
        self.repeat_seconds, self.clock = repeat_seconds, clock
        self.last: dict[str, float] = {}
        self.sent = self.failed = self.skipped = 0
        self.last_error: str | None = None
        self.q: queue.Queue = queue.Queue(maxsize=500)
        self.background = background
        if background:
            threading.Thread(target=self._worker, daemon=True, name="telegram-alerts").start()

    @classmethod
    def from_env(cls, label: str, env=os.environ) -> "Alerts | None":
        token, chat = env.get("TELEGRAM_BOT_TOKEN", "").strip(), env.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat:
            return None
        types = [t.strip().upper() for t in env.get("TELEGRAM_ALERTS", "").split(",") if t.strip()] or ALERT_TYPES
        return cls(telegram_sender(token, chat), label, types)

    def format(self, type_: str, message: str) -> str:
        return f"{ICONS.get(type_, '•')} {self.label} · {type_.replace('_', ' ').lower()}\n{message}"

    def on_event(self, e) -> None:
        """EventLog listener."""
        if e.type not in self.types:
            return
        now = self.clock()
        if e.type not in NEVER_LIMITED and now - self.last.get(e.type, -1e18) < self.repeat_seconds:
            self.skipped += 1
            return
        self.last[e.type] = now
        self.push(self.format(e.type, e.message))

    def push(self, text: str) -> None:
        if not self.background:
            self._send(text)
            return
        try:
            self.q.put_nowait(text)
        except queue.Full:
            self.skipped += 1

    def _send(self, text: str) -> None:
        try:
            self.send(text[:4000])
            self.sent += 1
        except Exception as e:                      # never reaches the robot
            self.failed += 1
            self.last_error = f"{type(e).__name__}: {e}"[:200]

    def _worker(self) -> None:
        while True:
            self._send(self.q.get())

    def status(self) -> dict:
        return {"enabled": True, "types": sorted(self.types), "sent": self.sent, "failed": self.failed,
                "skipped_repeats": self.skipped, "last_error": self.last_error}
