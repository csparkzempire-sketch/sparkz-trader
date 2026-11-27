"""
Trade approvals: the assistant scans every market the MT5 bridge watches (forex, crypto, indices, stocks,
commodities: whatever `--watch` / BRIDGE_WATCH lists) and PROPOSES trades. Nothing is placed until you approve a
proposal on the dashboard (Trade approvals page) or with the Approve button on Telegram.

  scan      when a candle of APPROVAL_TF (default 1d) closes on a watched market, the assistant analyses it the
            same way the gold robot does; a BUY or SELL with a plan becomes a PENDING proposal: entry, stop loss,
            take profit, confidence, the strategies behind it. One proposal or trade per market at a time.
  approve   checked again at that moment: the proposal has not expired, the MT5 account is fit to trade (same
            checks as the robot: demo unless MT5_LIVE + --live, algo trading on, the daily loss stop), fewer than
            APPROVAL_MAX_OPEN approved trades are open, the market's price is fresh, still between the stop and
            the target, has not run more than APPROVAL_MAX_DRIFT_R x the risk past the planned entry, and still
            offers at least 1:1. Then one market order goes to the bridge (OPEN_PLAN): the bridge sizes it from
            MT5's contract data so the stop loss costs about APPROVAL_RISK in the account currency (capped by its
            --max-approval-lots), and sets the stop loss and take profit in MT5 itself.
  manage    the trade lives in MT5 with its stop loss and take profit; it is followed through the bridge's
            account reports until it closes. You can close it early from the dashboard.

Settings (environment / .env): APPROVALS=on|off, APPROVAL_TF, APPROVAL_RISK (default 10), APPROVAL_MAX_OPEN (3),
APPROVAL_MIN_CONFIDENCE (0.55), APPROVAL_EXPIRY_MINUTES (default by timeframe), APPROVAL_MAX_DRIFT_R (0.5),
APPROVAL_INCLUDE_ROBOT_SYMBOL (false: the gold robot already trades its own market).

Evidence: the assistant has been tested on gold (daily: positive, not yet proven) and on EURUSD, GBPUSD and
USDJPY (technical report, section 23). Other markets are untested: approve with that in mind.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.assistant.context import TF_MINUTES
from app.assistant.engine import Assistant
from app.config import DATA_DIR
from app.execution.mt5_executor import ORDERS, OrderBook
from app.market.watch import WATCH, WATCH_TFS, WatchStore

OPEN_STATES = ("PENDING", "SENT", "OPEN", "CLOSING")
EXPIRY_DEFAULT = {"5m": 10, "15m": 20, "1h": 45, "4h": 120, "1d": 360}
ORDER_BASE = 2_000_000_000            # order ids of approved trades (the robot's ids stay far below)


class ApprovalError(Exception):
    pass


@dataclass
class ApprovalConfig:
    enabled: bool = True
    timeframe: str = "1d"
    risk: float = 10.0
    max_open: int = 3
    min_confidence: float = 0.55
    expiry_minutes: float = 360.0
    max_drift_r: float = 0.5
    include_robot_symbol: bool = False
    quote_max_age: float = 120.0

    @classmethod
    def from_env(cls, env=os.environ) -> "ApprovalConfig":
        tf = env.get("APPROVAL_TF", "1d").strip()
        if tf not in WATCH_TFS:
            tf = "1d"

        def num(key, default, lo, hi):
            try:
                return min(hi, max(lo, float(env.get(key, default))))
            except ValueError:
                return default
        return cls(enabled=env.get("APPROVALS", "on").strip().lower() not in ("off", "0", "false", "no"),
                   timeframe=tf, risk=num("APPROVAL_RISK", 10.0, 0.5, 100000.0),
                   max_open=int(num("APPROVAL_MAX_OPEN", 3, 1, 20)),
                   min_confidence=num("APPROVAL_MIN_CONFIDENCE", 0.55, 0.0, 1.0),
                   expiry_minutes=num("APPROVAL_EXPIRY_MINUTES", EXPIRY_DEFAULT[tf], 1, 10080),
                   max_drift_r=num("APPROVAL_MAX_DRIFT_R", 0.5, 0.0, 2.0),
                   include_robot_symbol=env.get("APPROVAL_INCLUDE_ROBOT_SYMBOL", "").strip().lower() in ("1", "true", "yes", "on"))


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


class ApprovalManager:
    def __init__(self, cfg: ApprovalConfig | None = None, book: OrderBook = ORDERS, watch: WatchStore = WATCH,
                 executor_fn=lambda: None, robot_symbol: str = "XAUUSD", clock=time.time,
                 path: Path | None = DATA_DIR / "approvals.json", notify=None):
        self.cfg = cfg or ApprovalConfig.from_env()
        self.book, self.watch, self.executor_fn, self.clock = book, watch, executor_fn, clock
        self.robot_symbol = robot_symbol.upper()
        self.path = path
        self.notify = notify                      # object with proposal(p) / message(text), or None
        self.lock = threading.RLock()
        self.proposals: dict[int, dict] = {}
        self.orders: dict[int, tuple[int, str]] = {}      # order id -> (proposal id, kind)
        self.scanned: dict[str, str] = {}                 # symbol -> last closed candle analysed
        self.seq = 0
        self.order_seq = 0
        self.last_scan: float | None = None
        self.scan_errors: dict[str, str] = {}
        self._stop = False
        self._thread: threading.Thread | None = None
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self.proposals = {int(p["id"]): p for p in data.get("proposals", [])}
            self.seq = int(data.get("seq", max(self.proposals, default=0)))
            self.order_seq = int(data.get("order_seq", 0))
            for p in self.proposals.values():         # orders in flight when the server stopped are gone
                if p["status"] == "SENT":
                    self._set(p, "FAILED", "the server restarted before MT5 answered; check MT5")
        except Exception:
            self.proposals = {}

    def _save(self) -> None:
        if not self.path:
            return
        try:
            keep = sorted(self.proposals.values(), key=lambda p: p["id"])[-300:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"seq": self.seq, "order_seq": self.order_seq, "proposals": keep}, default=str))
            tmp.replace(self.path)
        except Exception:
            pass

    def _set(self, p: dict, status: str, note: str) -> None:
        p["status"] = status
        p.setdefault("events", []).append({"time": _iso(self.clock()), "status": status, "note": note})
        p["events"] = p["events"][-30:]

    def _tell(self, text: str) -> None:
        if self.notify is not None:
            try:
                self.notify.message(text)
            except Exception:
                pass

    # ------------------------------------------------------------------ scanning
    def markets(self) -> list[str]:
        out = []
        for w in self.watch.symbols():
            if w["symbol"] == self.robot_symbol and not self.cfg.include_robot_symbol:
                continue
            out.append(w["symbol"])
        return out

    def scan(self) -> list[dict]:
        """Analyse each watched market whose APPROVAL_TF candle closed since the last look."""
        if not self.cfg.enabled:
            return []
        tf, new = self.cfg.timeframe, []
        self.last_scan = self.clock()
        for sym in self.markets():
            try:
                p = self._scan_one(sym, tf)
                if p is not None:
                    new.append(p)
                self.scan_errors.pop(sym, None)
            except Exception as e:                     # one market never stops the others
                self.scan_errors[sym] = f"{type(e).__name__}: {e}"[:200]
        return new

    def _scan_one(self, sym: str, tf: str) -> dict | None:
        floor = 60 if TF_MINUTES[tf] >= 240 else 0        # slow timeframes read 1h and above, as tested
        frames = {t: self.watch.frame(sym, t, closed_only=True) for t in WATCH_TFS if TF_MINUTES[t] >= floor}
        frames = {t: df for t, df in frames.items() if len(df) >= 60}
        if tf not in frames:
            return None
        last = pd.Timestamp(frames[tf]["timestamp"].iloc[-1])
        if self.scanned.get(sym) == str(last):
            return None
        self.scanned[sym] = str(last)
        closed_at = last + pd.Timedelta(minutes=TF_MINUTES[tf])
        if self.clock() - closed_at.timestamp() > self.cfg.expiry_minutes * 60:
            return None                                   # an old candle (e.g. just after a restart): not news
        with self.lock:
            if any(p["symbol"] == sym and p["status"] in OPEN_STATES for p in self.proposals.values()):
                return None
        tick = self.watch.tick(sym)
        spread = max(0.0, tick["ask"] - tick["bid"]) if tick else 0.0
        rep = Assistant(self.cfg.min_confidence).analyze(frames, spread, tf)
        plan = rep.get("plan")
        if rep.get("action") not in ("BUY", "SELL") or not plan:
            return None
        side = rep["action"]
        entry, stop, target = float(plan["entry"]), float(plan["stop"]), float(plan["target"])
        risk = abs(entry - stop)
        if risk <= 0 or (target - entry) * (1 if side == "BUY" else -1) <= 0:
            return None
        with self.lock:
            self.seq += 1
            p = {"id": self.seq, "symbol": sym, "broker_symbol": self.watch.broker_symbol(sym) or sym,
                 "timeframe": tf, "side": side, "entry": entry, "stop": stop, "target": target,
                 "risk_reward": round(abs(target - entry) / risk, 2), "strategy": plan.get("strategy"),
                 "plan_timeframe": plan.get("tf"), "confidence_pct": rep.get("confidence_pct"),
                 "bias": rep.get("higher_timeframe_bias"), "reasons": list(rep.get("reasons") or [])[:4],
                 "top_strategies": list((rep.get("strategy_fit_pct") or {}).items())[:3],
                 "past_hit_rate_pct": (plan.get("history") or {}).get("hit_rate_pct"),
                 "past_samples": (plan.get("history") or {}).get("samples"),
                 "digits": self.watch.digits(sym), "candle": last.isoformat(), "created_at": _iso(self.clock()),
                 "expires_at": _iso(self.clock() + self.cfg.expiry_minutes * 60),
                 "risk_per_trade": self.cfg.risk, "events": []}
            self._set(p, "PENDING", f"{side} setup on the {tf} close ({rep.get('confidence_pct')}% confidence)")
            self.proposals[p["id"]] = p
            self._save()
        if self.notify is not None:
            try:
                self.notify.proposal(p)
            except Exception:
                pass
        return p

    # ------------------------------------------------------------------ your decisions
    def approve(self, pid: int, via: str = "dashboard") -> dict:
        with self.lock:
            p = self.proposals.get(pid)
            if p is None:
                raise ApprovalError(f"no proposal #{pid}")
            if p["status"] != "PENDING":
                raise ApprovalError(f"proposal #{pid} is {p['status'].lower()}, not waiting for approval")
            if self.clock() > datetime.fromisoformat(p["expires_at"]).timestamp():
                self._set(p, "EXPIRED", "approved too late")
                self._save()
                raise ApprovalError(f"proposal #{pid} expired at {p['expires_at'][:16]} UTC")
            ex = self.executor_fn()
            if ex is None:
                raise ApprovalError("trading is off: the server runs EXECUTION_MODE=SIMULATION. Approvals need "
                                    "MT5_DEMO (or MT5_LIVE) and the bridge running with --trade")
            problem = ex.account_problem()
            if problem:
                raise ApprovalError(problem)
            acc = self.book.account_report()
            if acc is None:
                raise ApprovalError("no recent report from the MT5 bridge (is window 2 running with --trade?)")
            if "approvals" not in (acc.get("features") or []):
                raise ApprovalError("the MT5 bridge is an older version that cannot place approved trades: update "
                                    "the SPARKZ files and restart (START_SPARKZ.bat)")
            busy = sum(q["status"] in ("SENT", "OPEN", "CLOSING") for q in self.proposals.values())
            if busy >= self.cfg.max_open:
                raise ApprovalError(f"{busy} approved trades are already open (limit {self.cfg.max_open}, APPROVAL_MAX_OPEN)")
            age = self.watch.age(p["symbol"])
            tick = self.watch.tick(p["symbol"])
            if tick is None or age is None or age > self.cfg.quote_max_age:
                raise ApprovalError(f"no fresh {p['symbol']} price from MT5 (is the bridge watching it?)")
            sign = 1 if p["side"] == "BUY" else -1
            price = tick["ask"] if sign == 1 else tick["bid"]
            risk = abs(p["entry"] - p["stop"])
            if (price - p["stop"]) * sign <= 0:
                self._set(p, "EXPIRED", f"price {price} already reached the stop loss")
                self._save()
                raise ApprovalError(f"the price ({price}) has already passed the stop loss: the setup failed")
            if (p["target"] - price) * sign <= 0:
                self._set(p, "EXPIRED", f"price {price} already reached the target")
                self._save()
                raise ApprovalError(f"the price ({price}) has already reached the take profit: the move is over")
            drift = (price - p["entry"]) * sign
            if drift > self.cfg.max_drift_r * risk:
                raise ApprovalError(f"the price ran {drift / risk:.2f} x the risk past the planned entry (limit "
                                    f"{self.cfg.max_drift_r:g}): entering now is chasing")
            rr_now = (p["target"] - price) * sign / ((price - p["stop"]) * sign)
            if rr_now < 1.0:
                raise ApprovalError(f"from the current price the trade offers only {rr_now:.2f}:1")
            self.order_seq += 1
            oid = ORDER_BASE + self.order_seq
            self.book.add({"id": oid, "owner": "approval", "kind": "OPEN_PLAN", "symbol": p["broker_symbol"],
                           "side": p["side"], "stop": p["stop"], "target": p["target"], "risk": self.cfg.risk,
                           "proposal": pid, "created_at": _iso(self.clock())})
            self.orders[oid] = (pid, "OPEN_PLAN")
            p["approved_price"], p["approved_via"] = price, via
            self._set(p, "SENT", f"approved on the {via} at {price}; sent to MT5")
            self._save()
            return p

    def reject(self, pid: int, via: str = "dashboard") -> dict:
        with self.lock:
            p = self.proposals.get(pid)
            if p is None:
                raise ApprovalError(f"no proposal #{pid}")
            if p["status"] != "PENDING":
                raise ApprovalError(f"proposal #{pid} is {p['status'].lower()}")
            self._set(p, "REJECTED", f"rejected on the {via}")
            self._save()
            return p

    def close(self, pid: int, via: str = "dashboard") -> dict:
        with self.lock:
            p = self.proposals.get(pid)
            if p is None or p["status"] != "OPEN" or not p.get("ticket"):
                raise ApprovalError(f"proposal #{pid} has no open trade to close")
            if "approvals" not in ((self.book.account_report() or {}).get("features") or []):
                raise ApprovalError("the MT5 bridge is an older version: update it, or close the trade in MT5")
            self.order_seq += 1
            oid = ORDER_BASE + self.order_seq
            self.book.add({"id": oid, "owner": "approval", "kind": "CLOSE_TICKET", "symbol": p["broker_symbol"],
                           "ticket": p["ticket"], "proposal": pid, "created_at": _iso(self.clock())})
            self.orders[oid] = (pid, "CLOSE_TICKET")
            self._set(p, "CLOSING", f"close requested on the {via}")
            self._save()
            return p

    # ------------------------------------------------------------------ follow-up
    def tick(self) -> None:
        with self.lock:
            changed = False
            for r in self.book.take_results("approval"):
                pid, kind = self.orders.pop(r["id"], (r["order"].get("proposal"), r["order"].get("kind")))
                p = self.proposals.get(pid)
                if p is None:
                    continue
                changed = True
                if kind == "OPEN_PLAN":
                    if r.get("status") == "FILLED":
                        p.update(fill_price=r.get("price"), volume=r.get("volume"),
                                 ticket=(r.get("tickets") or [None])[0], opened_at=r.get("time"), seen=False, missing=0)
                        self._set(p, "OPEN", f"filled in MT5: {r.get('volume')} lots at {r.get('price')}")
                        self._tell(f"✅ #{pid} {p['side']} {p['symbol']} opened: {r.get('volume')} lots at "
                                   f"{r.get('price')} (SL {p['stop']}, TP {p['target']})")
                    else:
                        self._set(p, "FAILED", f"MT5 refused: {r.get('message')}")
                        self._tell(f"⛔ #{pid} {p['side']} {p['symbol']} was not placed: {r.get('message')}")
                elif kind == "CLOSE_TICKET":
                    if r.get("status") == "FILLED":
                        p["exit_price"] = r.get("price")
                        p["missing"] = 2                   # the next account report without it books the close
                    else:
                        self._set(p, "OPEN", f"close refused by MT5: {r.get('message')}")
            for o in self.book.expire("approval"):
                pid, _ = self.orders.pop(o["id"], (o.get("proposal"), None))
                p = self.proposals.get(pid)
                if p is not None and p["status"] in ("SENT", "CLOSING"):
                    self._set(p, "FAILED" if p["status"] == "SENT" else "OPEN",
                              "the MT5 bridge did not collect the order in time (is it running with --trade?)")
                    changed = True
            now = self.clock()
            for p in self.proposals.values():
                if p["status"] == "PENDING" and now > datetime.fromisoformat(p["expires_at"]).timestamp():
                    self._set(p, "EXPIRED", "not approved in time")
                    changed = True
            changed |= self._follow_positions()
            if changed:
                self._save()

    def _follow_positions(self) -> bool:
        acc = self.book.account_report()
        if acc is None:
            return False
        seq = self.book.account_seq
        live = acc.get("approved_positions") or []
        changed = False
        for p in self.proposals.values():
            if p["status"] not in ("OPEN", "CLOSING") or p.get("last_seq") == seq:
                continue
            p["last_seq"] = seq
            pos = next((x for x in live if x.get("ticket") == p.get("ticket")
                        or x.get("comment", "").startswith(f"SPARKZ-A{p['id']}")), None)
            if pos is not None:
                p.update(seen=True, missing=0, profit=pos.get("profit"), current=pos.get("price_current"),
                         ticket=pos.get("ticket"), sl_mt5=pos.get("sl"), tp_mt5=pos.get("tp"))
                continue
            p["missing"] = p.get("missing", 0) + 1
            if p["missing"] >= (2 if p.get("seen") else 4):
                last = p.get("current") or p.get("exit_price")
                why = "closed"
                if last is not None and p.get("fill_price") is not None:
                    sign = 1 if p["side"] == "BUY" else -1
                    if (last - p["target"]) * sign >= -abs(p["target"] - p["fill_price"]) * 0.05:
                        why = "take profit"
                    elif (last - p["stop"]) * sign <= abs(p["fill_price"] - p["stop"]) * 0.05:
                        why = "stop loss"
                p["closed_at"], p["close_reason"] = _iso(self.clock()), why
                self._set(p, "CLOSED", f"no longer open in MT5 ({why}); last profit seen {p.get('profit')}")
                self._tell(f"🏁 #{p['id']} {p['side']} {p['symbol']} closed ({why}); about {p.get('profit')} "
                           f"{acc.get('currency', '')}")
                changed = True
        return changed

    # ------------------------------------------------------------------ views
    def snapshot(self) -> dict:
        with self.lock:
            ps = sorted(self.proposals.values(), key=lambda p: p["id"], reverse=True)
            ex = self.executor_fn()
            acc = self.book.account_report()
            tracked = {p.get("ticket") for p in ps}
            closed = [p for p in ps if p["status"] == "CLOSED" and p.get("profit") is not None]
            return {"config": self.cfg.__dict__, "markets": self.markets(), "robot_symbol": self.robot_symbol,
                    "trading": ex is not None, "problem": None if ex is None else ex.account_problem(),
                    "last_scan": None if self.last_scan is None else _iso(self.last_scan),
                    "scan_errors": dict(self.scan_errors),
                    "pending": [p for p in ps if p["status"] == "PENDING"],
                    "active": [p for p in ps if p["status"] in ("SENT", "OPEN", "CLOSING")],
                    "history": [p for p in ps if p["status"] not in OPEN_STATES][:100],
                    "untracked_positions": [x for x in (acc or {}).get("approved_positions", [])
                                            if x.get("ticket") not in tracked],
                    "summary": {"closed": len(closed), "won": sum(p["profit"] > 0 for p in closed),
                                "net_profit": round(sum(p["profit"] for p in closed), 2),
                                "currency": (acc or {}).get("currency", "")}}

    # ------------------------------------------------------------------ loop
    def start(self, scan_every: float = 30.0, tick_every: float = 2.0) -> None:
        if self._thread is not None or not self.cfg.enabled:
            return
        self._stop = False

        def loop():
            last_scan = 0.0
            while not self._stop:
                try:
                    self.tick()
                    if time.time() - last_scan >= scan_every:
                        last_scan = time.time()
                        self.scan()
                except Exception:
                    pass
                time.sleep(tick_every)
        self._thread = threading.Thread(target=loop, daemon=True, name="approvals")
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._thread = None
