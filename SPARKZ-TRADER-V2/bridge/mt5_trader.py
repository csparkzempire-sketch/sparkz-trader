"""
MT5 order execution for SPARKZ TRADER V2: a DEMO account, or a REAL account when deliberately allowed.

Loaded by mt5_bridge.py only when it runs with --trade. The server must run with EXECUTION_MODE=MT5_DEMO (demo
only) or MT5_LIVE (real allowed too), and MARKET_DATA_PROVIDER=BRIDGE; otherwise it hands out no orders.
A real account also needs the bridge's --live (typed confirmation at start).

Every couple of seconds the bridge:
  1. reports the MT5 account (demo or real, balance, equity, free margin, SPARKZ positions; never the login);
  2. collects the robot's orders (GET /api/bridge/orders) and, for each one,
       - refuses it if MT5 is not on a DEMO account (unless --live was confirmed at start),
         if algo trading is off in MT5, if it is older than --max-age seconds, or above --max-lots;
       - opens at market with a protective stop loss, or closes every SPARKZ position (magic number) of the
         symbol;
  3. reports MT5's result (POST /api/bridge/orders/result): the fill price and volume, or the refusal reason.

SPARKZ positions carry magic number MAGIC and the comment "SPARKZ"; positions you open by hand are never touched.

Trades you approve on the dashboard (Trade approvals page; any market the bridge watches) arrive as OPEN_PLAN
orders: the lot size is worked out HERE from MT5's own contract data so the stop loss risks about `risk` in the
account currency (never more than --max-approval-lots), and the stop loss and take profit are set in MT5 on the
order itself. They carry magic number MAGIC_APPROVED and the comment "SPARKZ-A<id>"; CLOSE_TICKET closes one of
them. The same account checks apply (demo unless --live, algo trading on, order age).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

MAGIC = 26_11_2026
COMMENT = "SPARKZ"
MAGIC_APPROVED = 26_11_2027
DONE = (10009, 10010)            # TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL


def http_get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"X-Bridge-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"server answered HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from None


class Trader:
    def __init__(self, mt5, symbol: str, server: str, token: str, post, get=http_get, allow_real: bool = False,
                 max_lots: float = 0.01, max_age: float = 20.0, deviation: int = 30, log=print,
                 max_approval_lots: float = 0.1):
        self.mt5, self.symbol, self.token = mt5, symbol, token
        base = server.rstrip("/")
        self.orders_url, self.result_url = base + "/api/bridge/orders", base + "/api/bridge/orders/result"
        self.post, self.get, self.log = post, get, log
        self.allow_real, self.max_lots, self.max_age, self.deviation = allow_real, max_lots, max_age, deviation
        self.max_approval_lots = max_approval_lots
        self.done: set[int] = set()          # order ids already handled (never executed twice)

    # ---------------------------------------------------------------- account
    def _c(self, name: str, default):
        return getattr(self.mt5, name, default)

    def is_demo(self, info) -> bool:
        return info is not None and info.trade_mode == self._c("ACCOUNT_TRADE_MODE_DEMO", 0)

    def check_account(self) -> None:
        """At start: stop at once on a real account (unless unlocked) or when algo trading is off."""
        info = self.mt5.account_info()
        if info is None:
            raise RuntimeError(f"cannot read the MT5 account: {self.mt5.last_error()}")
        if not self.is_demo(info) and not self.allow_real:
            raise RuntimeError("MT5 is logged into a REAL (or contest) account. Without --live the bridge trades "
                               "DEMO accounts only: log MT5 into a demo account, or add --live to trade real money")
        term = self.mt5.terminal_info()
        if term is not None and not term.trade_allowed:
            self.log("WARNING: algo trading is OFF in MT5 (the Algo Trading button): orders will be refused")
        self.log(f"trading enabled on a {'DEMO' if self.is_demo(info) else 'REAL'} account "
                 f"({info.currency}, balance {info.balance:.2f}); max {self.max_lots:g} lots per order")

    def _positions(self) -> list:
        pos = self.mt5.positions_get(symbol=self.symbol) or ()
        return [p for p in pos if p.magic == MAGIC]

    def account_payload(self) -> dict | None:
        info = self.mt5.account_info()
        if info is None:
            return None
        term = self.mt5.terminal_info()
        return {"demo": self.is_demo(info), "real_allowed": self.allow_real,
                "trade_allowed": bool(term is not None and term.trade_allowed and info.trade_allowed),
                "currency": str(info.currency)[:8], "balance": float(info.balance), "equity": float(info.equity),
                "margin_free": float(info.margin_free), "leverage": int(info.leverage),
                "positions": [{"ticket": int(p.ticket), "side": "BUY" if p.type == self._c("POSITION_TYPE_BUY", 0)
                               else "SELL", "volume": float(p.volume), "price_open": float(p.price_open),
                               "sl": float(p.sl), "profit": float(p.profit)} for p in self._positions()],
                "approved_positions": self._approved_payload(),
                "features": ["approvals"]}           # this bridge understands OPEN_PLAN / CLOSE_TICKET

    def _approved_payload(self) -> list[dict]:
        out = []
        for p in self.mt5.positions_get() or ():
            if p.magic != MAGIC_APPROVED:
                continue
            out.append({"ticket": int(p.ticket), "symbol": str(p.symbol)[:32],
                        "side": "BUY" if p.type == self._c("POSITION_TYPE_BUY", 0) else "SELL",
                        "volume": float(p.volume), "price_open": float(p.price_open), "sl": float(p.sl),
                        "tp": float(p.tp), "profit": float(p.profit), "comment": str(p.comment)[:32],
                        "price_current": float(getattr(p, "price_current", 0.0))})
        return out

    # ---------------------------------------------------------------- orders
    def _filling(self, symbol: str | None = None) -> int:
        info = self.mt5.symbol_info(symbol or self.symbol)
        fm = getattr(info, "filling_mode", 0) if info is not None else 0
        if fm & 1:
            return self._c("ORDER_FILLING_FOK", 0)
        if fm & 2:
            return self._c("ORDER_FILLING_IOC", 1)
        return self._c("ORDER_FILLING_RETURN", 2)

    def _deal(self, side: str, volume: float, position: int | None = None, sl: float | None = None):
        tick = self.mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return None, "no price from MT5"
        price = tick.ask if side == "BUY" else tick.bid
        req = {"action": self._c("TRADE_ACTION_DEAL", 1), "symbol": self.symbol, "volume": float(volume),
               "type": self._c("ORDER_TYPE_BUY", 0) if side == "BUY" else self._c("ORDER_TYPE_SELL", 1),
               "price": price, "deviation": self.deviation, "magic": MAGIC, "comment": COMMENT,
               "type_time": self._c("ORDER_TIME_GTC", 0), "type_filling": self._filling()}
        if position is not None:
            req["position"] = position
        if sl is not None:
            digits = getattr(self.mt5.symbol_info(self.symbol), "digits", 2)
            req["sl"] = round(price - sl if side == "BUY" else price + sl, digits)
        res = self.mt5.order_send(req)
        if res is None:
            return None, f"order_send failed: {self.mt5.last_error()}"
        if res.retcode not in DONE:
            return None, f"retcode {res.retcode}: {res.comment}"
        return res, ""

    def refuse_reason(self, o: dict) -> str | None:
        info = self.mt5.account_info()
        if not self.is_demo(info) and not self.allow_real:
            return "MT5 is not on a DEMO account"
        term = self.mt5.terminal_info()
        if o["kind"] in ("OPEN_PLAN",) and term is not None and not term.trade_allowed:
            return "algo trading is OFF in MT5 (press the Algo Trading button)"
        if o.get("age_seconds", 0) > self.max_age:
            return f"order is {o['age_seconds']:.0f} s old (limit {self.max_age:g} s): not sent late"
        if o["kind"] == "OPEN" and o["volume"] > self.max_lots + 1e-9:
            return f"{o['volume']:g} lots is above the bridge's --max-lots {self.max_lots:g}"
        return None

    # ---------------------------------------------------------------- approved trades (any watched market)
    def plan_lots(self, info, entry: float, stop: float, risk: float) -> tuple[float | None, str]:
        """Lots so that the stop loss costs about `risk` (account currency), from MT5's tick value and lot steps."""
        tick_value = float(getattr(info, "trade_tick_value", 0) or 0)
        tick_size = float(getattr(info, "trade_tick_size", 0) or getattr(info, "point", 0) or 0)
        vmin = float(getattr(info, "volume_min", 0.01) or 0.01)
        vstep = float(getattr(info, "volume_step", 0.01) or 0.01)
        vmax = float(getattr(info, "volume_max", 100.0) or 100.0)
        if tick_value <= 0 or tick_size <= 0:
            return None, "MT5 gives no tick value for this market: cannot size the trade"
        loss_per_lot = abs(entry - stop) / tick_size * tick_value
        if loss_per_lot <= 0:
            return None, "the stop loss is at the entry price"
        raw = risk / loss_per_lot
        lots = int(raw / vstep + 1e-9) * vstep
        if lots < vmin:
            if vmin * loss_per_lot > risk * 1.5:
                return None, (f"the smallest lot ({vmin:g}) would risk {vmin * loss_per_lot:.2f} at the stop loss, "
                              f"more than 1.5 x your {risk:g} per trade")
            lots = vmin
        cap = min(vmax, self.max_approval_lots)
        if lots > cap:
            lots = int(cap / vstep + 1e-9) * vstep
            if lots < vmin:
                return None, f"--max-approval-lots {self.max_approval_lots:g} is below this market's smallest lot {vmin:g}"
        return round(lots, 8), ""

    def open_plan(self, o: dict, now: str) -> dict:
        sym = o["symbol"]
        if not self.mt5.symbol_select(sym, True):
            return {"id": o["id"], "status": "REJECTED", "message": f"{sym} is not in this MT5 terminal"}
        info, tick = self.mt5.symbol_info(sym), self.mt5.symbol_info_tick(sym)
        if info is None or tick is None or not tick.bid or not tick.ask:
            return {"id": o["id"], "status": "REJECTED", "message": f"no price for {sym} from MT5 (market closed?)"}
        side, stop, target = o["side"], float(o["stop"]), float(o["target"])
        price = tick.ask if side == "BUY" else tick.bid
        sign = 1 if side == "BUY" else -1
        if (price - stop) * sign <= 0 or (target - price) * sign <= 0:
            return {"id": o["id"], "status": "REJECTED",
                    "message": f"price {price} is no longer between the stop loss {stop} and the take profit {target}"}
        lots, why = self.plan_lots(info, price, stop, float(o["risk"]))
        if lots is None:
            return {"id": o["id"], "status": "REJECTED", "message": why}
        digits = int(getattr(info, "digits", 5))
        req = {"action": self._c("TRADE_ACTION_DEAL", 1), "symbol": sym, "volume": float(lots),
               "type": self._c("ORDER_TYPE_BUY", 0) if side == "BUY" else self._c("ORDER_TYPE_SELL", 1),
               "price": price, "sl": round(stop, digits), "tp": round(target, digits), "deviation": self.deviation,
               "magic": MAGIC_APPROVED, "comment": f"SPARKZ-A{o['proposal']}"[:31],
               "type_time": self._c("ORDER_TIME_GTC", 0), "type_filling": self._filling(sym)}
        res = self.mt5.order_send(req)
        if res is None:
            return {"id": o["id"], "status": "REJECTED", "message": f"order_send failed: {self.mt5.last_error()}"}
        if res.retcode not in DONE:
            return {"id": o["id"], "status": "REJECTED", "message": f"retcode {res.retcode}: {res.comment}"}
        return {"id": o["id"], "status": "FILLED", "price": float(res.price), "volume": float(res.volume), "time": now,
                "tickets": [int(res.order)], "message": f"opened {lots:g} lots"}

    def close_ticket(self, o: dict, now: str) -> dict:
        pos = [p for p in (self.mt5.positions_get(ticket=int(o["ticket"])) or ()) if p.magic == MAGIC_APPROVED]
        if not pos:
            return {"id": o["id"], "status": "FILLED", "price": None, "volume": 0.0, "time": now,
                    "message": "the position was already closed in MT5"}
        p = pos[0]
        tick = self.mt5.symbol_info_tick(p.symbol)
        if tick is None:
            return {"id": o["id"], "status": "REJECTED", "message": f"no price for {p.symbol}"}
        buy = p.type == self._c("POSITION_TYPE_BUY", 0)
        req = {"action": self._c("TRADE_ACTION_DEAL", 1), "symbol": p.symbol, "volume": float(p.volume),
               "type": self._c("ORDER_TYPE_SELL", 1) if buy else self._c("ORDER_TYPE_BUY", 0),
               "price": tick.bid if buy else tick.ask, "position": int(p.ticket), "deviation": self.deviation,
               "magic": MAGIC_APPROVED, "comment": "SPARKZ-A close", "type_time": self._c("ORDER_TIME_GTC", 0),
               "type_filling": self._filling(p.symbol)}
        res = self.mt5.order_send(req)
        if res is None or res.retcode not in DONE:
            why = f"order_send failed: {self.mt5.last_error()}" if res is None else f"retcode {res.retcode}: {res.comment}"
            return {"id": o["id"], "status": "REJECTED", "message": why}
        return {"id": o["id"], "status": "FILLED", "price": float(res.price), "volume": float(res.volume), "time": now,
                "tickets": [int(p.ticket)], "message": "closed"}

    def execute(self, o: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        why = self.refuse_reason(o)
        if why:
            return {"id": o["id"], "status": "REJECTED", "message": why}
        if o["kind"] == "OPEN_PLAN":
            return self.open_plan(o, now)
        if o["kind"] == "CLOSE_TICKET":
            return self.close_ticket(o, now)
        if o["kind"] == "OPEN":
            res, err = self._deal(o["side"], o["volume"], sl=o.get("protective_distance"))
            if res is None:
                return {"id": o["id"], "status": "REJECTED", "message": err}
            return {"id": o["id"], "status": "FILLED", "price": float(res.price), "volume": float(res.volume),
                    "time": now, "tickets": [int(res.order)], "message": "opened"}
        # CLOSE: every SPARKZ position of the symbol
        pos = self._positions()
        if not pos:
            tick = self.mt5.symbol_info_tick(self.symbol)
            px = (tick.bid if o["side"] == "SELL" else tick.ask) if tick else None
            return {"id": o["id"], "status": "FILLED", "price": px, "volume": 0.0, "time": now,
                    "message": "no SPARKZ position was open in MT5"}
        filled, value, tickets, errors = 0.0, 0.0, [], []
        for p in pos:
            side = "SELL" if p.type == self._c("POSITION_TYPE_BUY", 0) else "BUY"
            res, err = self._deal(side, p.volume, position=p.ticket)
            if res is None:
                errors.append(f"#{p.ticket}: {err}")
                continue
            filled += float(res.volume)
            value += float(res.price) * float(res.volume)
            tickets.append(int(p.ticket))
        if errors and not filled:
            return {"id": o["id"], "status": "REJECTED", "message": "; ".join(errors)[:300]}
        return {"id": o["id"], "status": "FILLED", "price": value / filled, "volume": filled, "time": now,
                "tickets": tickets, "message": ("; ".join(errors) if errors else "closed")[:300]}

    def step(self) -> list[dict]:
        data = self.get(self.orders_url, self.token)
        out = []
        for o in data.get("orders", []):
            if o["id"] in self.done:
                continue
            self.done.add(o["id"])
            r = self.execute(o)
            what = o.get("symbol") or self.symbol
            self.log(f"order {o['id']} {o['kind']} {what} {o.get('side', '')} {o.get('volume', '')}: "
                     f"{r['status']} {r.get('message', '')}")
            self.post(self.result_url, self.token, r)
            out.append(r)
        return out
