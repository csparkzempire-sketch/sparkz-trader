"""
MT5 DEMO execution: the robot's approved orders are sent to the user's own MetaTrader 5 terminal through the
bridge (bridge/mt5_bridge.py --trade, which loads bridge/mt5_trader.py), and the fills MT5 reports come back.

  EXECUTION_MODE=MT5_DEMO   (server; needs MARKET_DATA_PROVIDER=BRIDGE)
  python bridge/mt5_bridge.py --symbol XAUUSDm --trade      (Windows, next to MT5)

The flow, one order at a time:

  Robot -> RiskManager -> MT5BridgeExecutor.submit -> ORDERS (queued)
        bridge: GET /api/bridge/orders -> order_send in MT5 -> POST /api/bridge/orders/result
  ORDERS (result) -> MT5BridgeExecutor.process -> Fill (MT5's price) -> strategy / account

Safety (each one is checked on its own; any one refuses the order):
- the bridge refuses to trade unless MT5 is logged into a DEMO account (a real account needs --allow-real-account
  on the bridge, a typed confirmation, and SPARKZ_ALLOW_REAL_ACCOUNT=I_ACCEPT_THE_RISK on the server);
- the server refuses to queue an order unless the bridge's last account report says demo (or real was unlocked
  on both sides);
- lots above execution.max_live_lots (default 0.01) are refused here and again by the bridge (--max-lots);
- every opening order carries a protective stop loss in MT5 (twice the basket loss limit), so a position is
  protected even if this server or the bridge stops;
- an order the bridge has not collected within ORDER_TTL seconds is dropped (never sent late), and the bridge
  refuses orders older than its own limit;
- if MT5 reports no SPARKZ position while the robot holds a basket (the protective stop hit, or the user closed
  it in MT5), the robot books the close at the current price and logs BROKER_CLOSED.

The robot's account on the dashboard stays its own ledger (fills at MT5's prices); the MT5 account itself
(balance, equity, open SPARKZ positions) is shown next to it as reported by the bridge.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

from app.config import Settings
from app.execution.order_intent import Fill, IntentType, OrderIntent
from app.execution.simulator import ExecutionAdapter
from app.market.instruments import Instrument
from app.market.providers.base import Tick

ORDER_TTL = 30.0            # seconds an order may wait for the bridge to collect it
ACCOUNT_TTL = 60.0          # an account report older than this does not count
REAL_UNLOCK = "I_ACCEPT_THE_RISK"


def real_account_unlocked() -> bool:
    return os.getenv("SPARKZ_ALLOW_REAL_ACCOUNT", "") == REAL_UNLOCK


class OrderBook:
    """Orders waiting for / sent to the bridge, their results, and the MT5 account as the bridge last saw it.
    Shared by the API (bridge side) and the executor (robot side)."""

    def __init__(self, clock=time.time):
        self.lock = threading.Lock()
        self.clock = clock
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.enabled = False
            self.orders: dict[int, dict] = {}          # id -> order (state QUEUED / SENT)
            self.results: list[dict] = []
            self.account: dict | None = None
            self.account_at: float | None = None
            self.account_seq = 0

    # ---------------------------------------------------------------- robot side
    def add(self, order: dict) -> None:
        with self.lock:
            self.orders[order["id"]] = {**order, "state": "QUEUED", "queued_at": self.clock()}

    def expire(self) -> list[dict]:
        """Orders the bridge never collected in time: dropped, never sent late."""
        now = self.clock()
        with self.lock:
            old = [o for o in self.orders.values() if o["state"] == "QUEUED" and now - o["queued_at"] > ORDER_TTL]
            for o in old:
                del self.orders[o["id"]]
        return old

    def cancel_queued(self) -> list[dict]:
        with self.lock:
            q = [o for o in self.orders.values() if o["state"] == "QUEUED"]
            for o in q:
                del self.orders[o["id"]]
        return q

    def take_results(self) -> list[dict]:
        with self.lock:
            out, self.results = self.results, []
        return out

    def open_orders(self) -> list[dict]:
        with self.lock:
            return list(self.orders.values())

    def account_report(self) -> dict | None:
        with self.lock:
            if self.account is None or self.account_at is None or self.clock() - self.account_at > ACCOUNT_TTL:
                return None
            return dict(self.account)

    # ---------------------------------------------------------------- bridge side
    def fetch(self) -> list[dict]:
        now = self.clock()
        with self.lock:
            out = []
            for o in self.orders.values():
                if o["state"] == "QUEUED":
                    o["state"], o["sent_at"] = "SENT", now
                    out.append({k: v for k, v in o.items() if k not in ("state", "queued_at", "sent_at")}
                               | {"age_seconds": round(now - o["queued_at"], 1)})
            return out

    def report(self, result: dict) -> bool:
        with self.lock:
            o = self.orders.pop(result["id"], None)
            if o is None:
                return False
            self.results.append({**result, "order": o})
            return True

    def set_account(self, account: dict) -> None:
        with self.lock:
            self.account, self.account_at = dict(account), self.clock()
            self.account_seq += 1


ORDERS = OrderBook()


class MT5BridgeExecutor(ExecutionAdapter):
    simulated = False
    label = "MT5 DEMO (via the bridge)"

    def __init__(self, inst: Instrument, settings: Settings, book: OrderBook | None = None):
        self.inst, self.s = inst, settings
        self.book = book or ORDERS
        self.book.enabled = True
        self.intents: dict[int, OrderIntent] = {}
        self.rejected: list[tuple[OrderIntent, str]] = []
        self.flat_reports = 0               # consecutive account reports with no SPARKZ position
        self._seen_seq = -1

    # ---------------------------------------------------------------- checks
    def account_problem(self) -> str | None:
        acc = self.book.account_report()
        if acc is None:
            return "no recent account report from the MT5 bridge (is it running with --trade?)"
        if not acc.get("trade_allowed", False):
            return "MT5 does not allow automated trading (press the Algo Trading button in MT5)"
        if not acc.get("demo", False) and not (acc.get("real_allowed") and real_account_unlocked()):
            return "MT5 is logged into a REAL account: SPARKZ trades demo accounts only"
        return None

    def protective_distance(self, lots: float) -> float:
        r = self.s.risk
        usd = r.max_basket_loss_usd if r.max_basket_loss_usd else r.initial_capital * r.max_basket_loss_percent / 100
        return round(2 * usd / max(lots * self.inst.contract_size, 1e-9), 2)

    # ---------------------------------------------------------------- adapter
    def submit(self, intent: OrderIntent, now: datetime, ref_mid: float | None = None) -> None:
        problem = None if intent.type == IntentType.CLOSE_BASKET else self.account_problem()
        cap = self.s.execution.max_live_lots
        if problem is None and intent.type != IntentType.CLOSE_BASKET and intent.lots > cap + 1e-9:
            problem = f"{intent.lots:g} lots is above the live limit of {cap:g} (execution.max_live_lots)"
        if problem:
            self.rejected.append((intent, problem))
            return
        side = intent.direction if intent.type != IntentType.CLOSE_BASKET else (
            "SELL" if intent.direction == "BUY" else "BUY")
        self.intents[intent.id] = intent
        self.book.add({"id": intent.id, "kind": "CLOSE" if intent.type == IntentType.CLOSE_BASKET else "OPEN",
                       "side": side, "volume": round(intent.lots, 2), "basket_id": intent.basket_id,
                       "reason": intent.reason[:60],
                       "protective_distance": None if intent.type == IntentType.CLOSE_BASKET
                       else self.protective_distance(intent.lots),
                       "created_at": now.isoformat()})

    def pending(self) -> list[OrderIntent]:
        ids = {o["id"] for o in self.book.open_orders()}
        return [i for k, i in self.intents.items() if k in ids]

    def cancel_all(self) -> list[OrderIntent]:
        """Orders not yet collected by the bridge are cancelled; one already sent to MT5 cannot be."""
        out = []
        for o in self.book.cancel_queued():
            it = self.intents.pop(o["id"], None)
            if it is not None:
                out.append(it)
        return out

    def take_rejections(self) -> list[tuple[OrderIntent, str]]:
        for o in self.book.expire():
            it = self.intents.pop(o["id"], None)
            if it is not None:
                self.rejected.append((it, f"the MT5 bridge did not collect the order within {ORDER_TTL:.0f} s; "
                                          "it was dropped, not sent"))
        out, self.rejected = self.rejected, []
        return out

    def process(self, tick: Tick) -> list[Fill]:
        fills = []
        for r in self.book.take_results():
            it = self.intents.pop(r["id"], None)
            if it is None:
                continue
            if r.get("status") != "FILLED":
                self.rejected.append((it, f"MT5 refused the order: {r.get('message') or r.get('status')}"))
                continue
            price = float(r.get("price") or tick.mid)
            t = datetime.fromisoformat(r["time"]) if r.get("time") else tick.time
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            fills.append(Fill(it.id, it.type, it.symbol, it.direction, float(r.get("volume") or it.lots), price,
                              tick.mid, tick.spread, 0.0, abs(float(r.get("commission") or 0.0)), t, it.basket_id,
                              it.reason, it.close_reason))
            self.flat_reports = 0
        return fills

    def broker_flat(self) -> bool | None:
        """False: MT5 holds SPARKZ positions. True: two consecutive fresh account reports showed none. None: not
        known yet."""
        acc = self.book.account_report()
        if acc is None:
            return None
        if acc.get("positions"):
            self.flat_reports = 0
            return False
        if self.book.account_seq != self._seen_seq:         # count each report once
            self._seen_seq = self.book.account_seq
            self.flat_reports += 1
        return True if self.flat_reports >= 2 else None

    def close_fill(self, intent: OrderIntent, tick: Tick) -> Fill:
        """The robot's record of a basket MT5 already closed (protective stop or closed by the user)."""
        price = tick.bid if intent.direction == "BUY" else tick.ask
        return Fill(intent.id, intent.type, intent.symbol, intent.direction, intent.lots, price, tick.mid,
                    tick.spread, 0.0, 0.0, tick.time, intent.basket_id, intent.reason, intent.close_reason)

    def describe(self) -> dict:
        acc = self.book.account_report()
        return {"kind": "MT5_DEMO", "label": self.label, "account": acc, "problem": self.account_problem(),
                "max_live_lots": self.s.execution.max_live_lots, "orders": self.book.open_orders()}
