"""
Telegram for trade approvals: each proposal arrives with Approve / Reject buttons, and the results follow.

Uses the same TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID as the alerts. A button press counts only when it comes from
that chat (anyone else who finds the bot is ignored). The bot reads presses with Telegram's getUpdates (long
polling); nothing else may poll the same bot. The token appears only in request URLs and is never logged.
APPROVAL_TELEGRAM_BUTTONS=off sends the proposals without buttons.
"""

from __future__ import annotations

import os
import threading
import time

API = "https://api.telegram.org/bot{token}/{method}"


def fmt(v, digits) -> str:
    if v is None:
        return "-"
    return f"{v:.{digits}f}" if isinstance(v, (int, float)) and digits is not None else str(v)


def proposal_text(p: dict) -> str:
    d = p.get("digits")
    top = ", ".join(f"{k.replace('_', ' ')} {v}%" for k, v in (p.get("top_strategies") or []))
    past = (f"\nPast record of this setup: target first in {p['past_hit_rate_pct']}% of {p['past_samples']}"
            if p.get("past_samples") else "")
    return (f"🔔 Proposal #{p['id']}: {p['side']} {p['symbol']} ({p['timeframe']})\n"
            f"Entry ≈ {fmt(p['entry'], d)}   SL {fmt(p['stop'], d)}   TP {fmt(p['target'], d)}   "
            f"(1:{p['risk_reward']})\n"
            f"Confidence {p.get('confidence_pct')}% · bias {p.get('bias')} · {str(p.get('strategy', '')).replace('_', ' ')}"
            f"{past}\nTop strategies now: {top}\n"
            f"Risk if approved: about {p['risk_per_trade']:g} (account currency) at the stop loss.\n"
            f"Expires {p['expires_at'][11:16]} UTC. Nothing is placed unless you approve.")


class TelegramApprovals:
    def __init__(self, token: str, chat_id: str, manager=None, buttons: bool = True, http=None):
        self.token, self.chat_id, self.manager, self.buttons = token, str(chat_id).strip(), manager, buttons
        self.offset = 0
        self.errors = 0
        self.last_error: str | None = None
        self._stop = False
        self._thread: threading.Thread | None = None
        if http is None:
            import httpx
            http = httpx.Client(timeout=40.0)
        self.http = http

    @classmethod
    def from_env(cls, manager=None, env=os.environ) -> "TelegramApprovals | None":
        token, chat = env.get("TELEGRAM_BOT_TOKEN", "").strip(), env.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat:
            return None
        return cls(token, chat, manager, env.get("APPROVAL_TELEGRAM_BUTTONS", "on").strip().lower() not in ("off", "0", "false"))

    def _call(self, method: str, **payload):
        r = self.http.post(API.format(token=self.token, method=method), json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"Telegram {method} answered HTTP {r.status_code}")      # never the URL
        return r.json().get("result")

    # ------------------------------------------------------------------ outgoing
    def proposal(self, p: dict) -> None:
        payload = {"chat_id": self.chat_id, "text": proposal_text(p), "disable_web_page_preview": True}
        if self.buttons:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"ap:{p['id']}"},
                                                            {"text": "❌ Reject", "callback_data": f"rj:{p['id']}"}]]}
        self._call("sendMessage", **payload)

    def message(self, text: str) -> None:
        self._call("sendMessage", chat_id=self.chat_id, text=text[:4000], disable_web_page_preview=True)

    # ------------------------------------------------------------------ incoming button presses
    def handle(self, update: dict) -> str | None:
        """One update from getUpdates. Returns what was done (for tests and the log)."""
        cq = update.get("callback_query")
        if not cq:
            return None
        chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
        if chat != self.chat_id:
            self._safe("answerCallbackQuery", callback_query_id=cq.get("id"), text="Not allowed")
            return "ignored: another chat"
        data = str(cq.get("data", ""))
        action, _, num = data.partition(":")
        if action not in ("ap", "rj") or not num.isdigit() or self.manager is None:
            self._safe("answerCallbackQuery", callback_query_id=cq.get("id"), text="Unknown button")
            return "ignored: unknown button"
        pid = int(num)
        try:
            if action == "ap":
                p = self.manager.approve(pid, via="Telegram")
                answer = f"Approved: sending #{pid} to MT5 at about {p.get('approved_price')}"
            else:
                self.manager.reject(pid, via="Telegram")
                answer = f"Rejected #{pid}"
        except Exception as e:
            answer = f"#{pid} not placed: {e}"
        self._safe("answerCallbackQuery", callback_query_id=cq.get("id"), text=answer[:190], show_alert=True)
        msg = cq.get("message") or {}
        if msg.get("message_id"):
            self._safe("editMessageReplyMarkup", chat_id=self.chat_id, message_id=msg["message_id"],
                       reply_markup={"inline_keyboard": []})
        self._safe("sendMessage", chat_id=self.chat_id, text=answer[:4000])
        return answer

    def _safe(self, method: str, **payload) -> None:
        try:
            self._call(method, **payload)
        except Exception as e:
            self.errors += 1
            self.last_error = f"{type(e).__name__}: {e}"[:200]

    def poll_once(self, timeout: int = 25) -> int:
        updates = self._call("getUpdates", offset=self.offset, timeout=timeout,
                             allowed_updates=["callback_query"]) or []
        for u in updates:
            self.offset = max(self.offset, int(u.get("update_id", 0)) + 1)
            try:
                self.handle(u)
            except Exception as e:
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"[:200]
        return len(updates)

    def start(self) -> None:
        if self._thread is not None or not self.buttons:
            return
        self._stop = False

        def loop():
            while not self._stop:
                try:
                    self.poll_once()
                except Exception as e:
                    self.errors += 1
                    self.last_error = f"{type(e).__name__}: {e}"[:200]
                    time.sleep(10)
        self._thread = threading.Thread(target=loop, daemon=True, name="telegram-approvals")
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._thread = None
