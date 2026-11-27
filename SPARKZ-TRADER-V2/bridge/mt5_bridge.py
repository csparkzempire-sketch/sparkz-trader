"""
MT5 -> SPARKZ TRADER V2 price bridge. It reads quotes and candles from your MetaTrader 5 terminal and sends them
to the V2 server. This file never calls an order function (a test in backend/tests checks it). Only with --trade
does it load mt5_trader.py, which executes the robot's orders in MT5 (demo account; a real account only with
--live, see that file).

Runs on the Windows computer where MetaTrader 5 is open and logged in (a broker demo account is enough; the
MetaTrader5 Python package works on Windows only).

  pip install MetaTrader5
  $env:SPARKZ_BRIDGE_TOKEN = "<the same secret the server has>"        # PowerShell; never put it in a file
  python mt5_bridge.py --symbol XAUUSD --server http://127.0.0.1:8000

  --symbol      the symbol as your broker names it (XAUUSD, XAUUSDm, GOLD, ...)
  --as          the V2 symbol it stands for (default XAUUSD)
  --utc-offset  broker server time minus UTC, in hours. MT5 reports times in the broker's server time; the
                bridge detects the offset while the market is open, and needs this option otherwise.
  --history     closed 1-minute candles sent at start (default 20000, about two weeks) so the robot's
                indicators are ready at once
  --interval    seconds between quote pushes (default 2)
  --trade       also execute the robot's orders in MT5 (demo account; the server needs EXECUTION_MODE=MT5_DEMO
                or MT5_LIVE). --max-lots caps each order (default 0.01).
  --watch       more markets for the dashboard's Chart page, as your broker names them, e.g.
                --watch EURUSDm,GBPUSDm,USDJPYm (the traded symbol is always included). Market data only.
  --live        with --trade: also allow a REAL account (asks you to type a confirmation; the server must run
                EXECUTION_MODE=MT5_LIVE). Real money: every study of this robot lost money after costs.

The server side: MARKET_DATA_PROVIDER=BRIDGE and the same SPARKZ_BRIDGE_TOKEN (see the V2 README).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

CHUNK = 2000
HTF = {"1h": ("TIMEFRAME_H1", 2000), "4h": ("TIMEFRAME_H4", 1000), "1d": ("TIMEFRAME_D1", 600)}
HTF_EVERY = 15 * 60          # seconds between higher-timeframe refreshes
WATCH_TF = {"5m": ("TIMEFRAME_M5", 2000), "15m": ("TIMEFRAME_M15", 1500), "1h": ("TIMEFRAME_H1", 1500),
            "4h": ("TIMEFRAME_H4", 1000), "1d": ("TIMEFRAME_D1", 600)}
WATCH_EVERY = 5              # seconds between chart updates (the latest candles of each watched market)
WATCH_MIN = 120              # candles per timeframe before a market's history counts as loaded (MT5 downloads a
                             # newly selected symbol's history in the background: until then, ask again)
WATCH_UPDATE = 3             # candles per timeframe in an update (the forming one and the last closed ones)


def detect_offset_hours(server_epoch: float, utc_epoch: float, tolerance: float = 120.0) -> float | None:
    """Broker server time minus UTC, to the nearest half hour, from a FRESH tick. None when the tick is not fresh
    (market closed), because then the gap is not just the time-zone offset."""
    diff = server_epoch - utc_epoch
    half_hours = round(diff / 1800)
    if abs(diff - half_hours * 1800) > tolerance:
        return None
    return half_hours / 2


def to_utc_iso(server_epoch: float, offset_hours: float) -> str:
    t = datetime.fromtimestamp(server_epoch, tz=timezone.utc) - timedelta(hours=offset_hours)
    return t.isoformat()


def bars_payload(rates, offset_hours: float, point: float | None = None) -> list[dict]:
    """MT5 candles -> UTC payload. With the symbol's `point`, each candle's recorded spread (in points) is sent
    in price units too."""
    rows = list(rates)
    names = getattr(getattr(rows[0], "dtype", None), "names", None) if rows else None
    has_spread = point is not None and "spread" in (names or ())
    return [{"time": to_utc_iso(float(r["time"]), offset_hours), "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["tick_volume"]),
             **({"spread": round(float(r["spread"]) * point, 6)} if has_spread else {})} for r in rows]


def tick_payload(tick, offset_hours: float) -> dict:
    return {"time": to_utc_iso(tick.time_msc / 1000.0, offset_hours), "bid": float(tick.bid), "ask": float(tick.ask)}


def http_post(url: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Bridge-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:              # the server's reason, never the token
        raise RuntimeError(f"server answered HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from None


class Bridge:
    def __init__(self, mt5, symbol: str, as_symbol: str, server: str, token: str, utc_offset: float | None = None,
                 history: int = 20000, interval: float = 2.0, post=http_post, clock=time.time, sleep=time.sleep,
                 log=print, trader=None, watch: list[str] | None = None):
        self.mt5, self.symbol, self.as_symbol = mt5, symbol, as_symbol.upper()
        self.url = server.rstrip("/") + "/api/bridge/push"
        self.token, self.offset, self.history, self.interval = token, utc_offset, history, interval
        self.post, self.clock, self.sleep, self.log = post, clock, sleep, log
        self.last_bar_time: float | None = None
        self.point: float | None = None
        self.htf_sent: float | None = None
        self.trader = trader                 # mt5_trader.Trader when run with --trade
        self.watch = list(dict.fromkeys([symbol] + [w for w in (watch or []) if w]))   # chart markets
        self.watch_url = server.rstrip("/") + "/api/bridge/watch"
        self.watch_sent: dict[str, bool] = {}            # symbol -> full history sent
        self.watch_tries: dict[str, int] = {}
        self.watch_at: float | None = None

    def connect(self) -> None:
        if not self.mt5.initialize():
            raise RuntimeError(f"cannot attach to the MetaTrader 5 terminal: {self.mt5.last_error()}")
        if not self.mt5.symbol_select(self.symbol, True):
            raise RuntimeError(f"symbol {self.symbol!r} not available in this terminal (check the broker's name for it)")
        info = self.mt5.symbol_info(self.symbol)
        self.point = float(info.point) if info is not None and getattr(info, "point", 0) else None
        if self.offset is None:
            tick = self.mt5.symbol_info_tick(self.symbol)
            self.offset = detect_offset_hours(tick.time_msc / 1000.0, self.clock()) if tick else None
            if self.offset is None:
                raise RuntimeError("cannot detect the broker's time offset (market closed?): pass --utc-offset")
        self.log(f"attached to MT5: {self.symbol} -> {self.as_symbol}, server time = UTC{self.offset:+g} h")

    def _send(self, tick=None, bars=None) -> dict:
        body = {"symbol": self.as_symbol, "broker_symbol": self.symbol, "tick": tick, "bars_1m": bars or []}
        if self.trader is not None:
            body["account"] = self.trader.account_payload()
        return self.post(self.url, self.token, body)

    def send_history(self) -> None:
        # position 0 is the candle still forming: start at 1, closed candles only
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_M1, 1, self.history)
        if rates is None or not len(rates):
            raise RuntimeError(f"no 1-minute history for {self.symbol}: {self.mt5.last_error()}")
        bars = bars_payload(rates, self.offset, self.point)
        for i in range(0, len(bars), CHUNK):
            self._send(bars=bars[i:i + CHUNK])
        self.last_bar_time = float(rates[-1]["time"])
        self.log(f"sent {len(bars)} closed 1-minute candles, last {bars[-1]['time']}")
        self.send_higher_timeframes()

    def send_higher_timeframes(self) -> None:
        """Closed 1h / 4h / daily candles for the market assistant (weeks of 1m candles cannot make a year
        of daily ones). Sent at start and every 15 minutes."""
        htf = {}
        for tf, (const, count) in HTF.items():
            code = getattr(self.mt5, const, None)
            rates = self.mt5.copy_rates_from_pos(self.symbol, code, 1, count) if code is not None else None
            if rates is not None and len(rates):
                htf[tf] = bars_payload(rates, self.offset)
        self.htf_sent = self.clock()
        if htf:
            self.post(self.url, self.token, {"symbol": self.as_symbol, "broker_symbol": self.symbol,
                                             "tick": None, "bars_1m": [], "bars_htf": htf})

    def send_watch(self) -> None:
        """Chart data for each watched market: the full history until MT5 has it, then the latest candles. A market
        the terminal does not have is reported once and dropped; nothing here stops the price feed."""
        self.watch_at = self.clock()
        for sym in list(self.watch):
            try:
                if sym not in self.watch_sent:
                    if not self.mt5.symbol_select(sym, True):
                        self.log(f"--watch: {sym!r} not in this terminal (check the name in MT5's Market Watch), skipped")
                        self.watch.remove(sym)
                        continue
                    self.watch_sent[sym] = False
                full = not self.watch_sent[sym]
                bars = {}
                for tf, (const, count) in WATCH_TF.items():
                    code = getattr(self.mt5, const, None)
                    rates = self.mt5.copy_rates_from_pos(sym, code, 0, count if full else WATCH_UPDATE) \
                        if code is not None else None
                    if rates is not None and len(rates):
                        rows = bars_payload(rates, self.offset)
                        rows[-1]["forming"] = True               # position 0: the candle still open
                        bars[tf] = rows
                tick = self.mt5.symbol_info_tick(sym)
                info = self.mt5.symbol_info(sym)
                body = {"broker_symbol": sym, "bars": bars,
                        "tick": tick_payload(tick, self.offset) if tick and tick.bid > 0 and tick.ask >= tick.bid else None,
                        "digits": int(info.digits) if info is not None and hasattr(info, "digits") else None}
                self.post(self.watch_url, self.token, body)
                if full:
                    loaded = sum(len(v) >= WATCH_MIN for v in bars.values())
                    sizes = ", ".join(f"{tf} {len(v)}" for tf, v in bars.items()) or "no candles yet"
                    if loaded >= 3:
                        self.watch_sent[sym] = True
                        self.log(f"chart: sent {sym} ({sizes})")
                    else:
                        self.watch_tries[sym] = self.watch_tries.get(sym, 0) + 1
                        if self.watch_tries[sym] in (1, 12, 60):
                            self.log(f"chart: waiting for MT5 to load {sym} history ({sizes}); open its chart in MT5 "
                                     "once if this lasts")
            except Exception as e:
                self.log(f"chart update for {sym} failed: {e}")

    def step(self) -> dict | None:
        if self.watch and (self.watch_at is None or self.clock() - self.watch_at >= WATCH_EVERY):
            self.send_watch()
        if self.htf_sent is not None and self.clock() - self.htf_sent >= HTF_EVERY:
            self.send_higher_timeframes()
        tick = self.mt5.symbol_info_tick(self.symbol)
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_M1, 1, 5)
        new = [r for r in (rates if rates is not None else []) if self.last_bar_time is None
               or float(r["time"]) > self.last_bar_time]
        if new:
            self.last_bar_time = float(new[-1]["time"])
        if tick is None and not new and self.trader is None:
            return None
        out = self._send(tick_payload(tick, self.offset) if tick else None, bars_payload(new, self.offset, self.point))
        if self.trader is not None:
            self.trader.step()
        return out

    def run(self, max_steps: int | None = None) -> None:
        self.connect()
        if self.trader is not None:
            self.trader.check_account()
        self.send_history()
        n = 0
        while max_steps is None or n < max_steps:
            try:
                self.step()
            except Exception as e:                   # keep running through network or server hiccups
                self.log(f"push failed: {e}")
            n += 1
            self.sleep(self.interval)


ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def load_env(path: str = ENV_FILE, environ=os.environ) -> int:
    """SPARKZ-TRADER-V2/.env (written by setup_sparkz.ps1) into the environment; variables already set win. Same
    rules as the server's reader: quoted values are taken as is, ` #` starts a comment, UTF-8 or UTF-16."""
    if not os.path.exists(path):
        return 0
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode("utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else data.decode("utf-8-sig", "replace")
    n = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        if v[:1] in ("'", '"') and v.find(v[0], 1) > 0:
            v = v[1:v.find(v[0], 1)]
        else:
            v = v.split(" #", 1)[0].strip()
        if k and v and k not in environ:
            environ[k] = v
            n += 1
    return n


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def parse_args(argv=None, environ=os.environ) -> argparse.Namespace:
    env = environ.get
    ap = argparse.ArgumentParser(description="Read-only MT5 price bridge for SPARKZ TRADER V2. Defaults come from "
                                             "SPARKZ-TRADER-V2/.env (BRIDGE_SYMBOL, BRIDGE_SERVER, BRIDGE_WATCH, "
                                             "BRIDGE_TRADE, BRIDGE_UTC_OFFSET) when it has them.")
    ap.add_argument("--symbol", default=env("BRIDGE_SYMBOL") or "XAUUSD")
    ap.add_argument("--as", dest="as_symbol", default="XAUUSD")
    ap.add_argument("--server", default=env("BRIDGE_SERVER") or "http://127.0.0.1:8000")
    ap.add_argument("--utc-offset", type=float, default=float(env("BRIDGE_UTC_OFFSET")) if env("BRIDGE_UTC_OFFSET") else None)
    ap.add_argument("--history", type=int, default=20000)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--watch", default=env("BRIDGE_WATCH") or "", help="more markets for the Chart page, e.g. EURUSDm,GBPUSDm,USDJPYm")
    ap.add_argument("--trade", action="store_true", default=_flag(env("BRIDGE_TRADE")),
                    help="execute the robot's orders (DEMO account only; .env BRIDGE_TRADE=true)")
    ap.add_argument("--max-lots", type=float, default=0.01)
    ap.add_argument("--max-approval-lots", type=float, default=float(env("BRIDGE_MAX_APPROVAL_LOTS") or 0.1),
                    help="largest lot size for a trade you approve on the dashboard (default 0.1)")
    ap.add_argument("--live", "--allow-real-account", dest="allow_real_account", action="store_true",
                    help="with --trade: also allow a REAL account (typed confirmation)")
    return ap.parse_args(argv)


def main(argv=None, environ=os.environ) -> None:
    load_env(environ=environ)
    env = environ.get
    a = parse_args(argv, environ)
    if a.allow_real_account and not a.trade:
        sys.exit("--live only makes sense with --trade")
    if a.allow_real_account:
        print("WARNING: REAL MONEY. Every study of this robot lost money after costs; you can lose what you trade.")
        if input("Type I ACCEPT THE RISK to allow a real account: ").strip().upper() != "I ACCEPT THE RISK":
            sys.exit("not confirmed: nothing started")
    token = env("SPARKZ_BRIDGE_TOKEN", "")
    if not token:
        sys.exit("set SPARKZ_BRIDGE_TOKEN in the environment or in SPARKZ-TRADER-V2/.env (run setup_sparkz.ps1)")
    try:
        import MetaTrader5 as mt5
    except ImportError:
        sys.exit("pip install MetaTrader5   (Windows only)")
    try:
        trader = None
        if a.trade:
            from mt5_trader import Trader
            trader = Trader(mt5, a.symbol, a.server, token, http_post, allow_real=a.allow_real_account,
                            max_lots=a.max_lots, max_approval_lots=a.max_approval_lots)
        Bridge(mt5, a.symbol, a.as_symbol, a.server, token, a.utc_offset, a.history, a.interval,
               trader=trader, watch=[w.strip() for w in a.watch.split(",") if w.strip()]).run()
    except KeyboardInterrupt:
        pass
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
