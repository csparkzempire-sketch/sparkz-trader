"""
MT5 -> SPARKZ TRADER V2 price bridge. READ-ONLY: it reads quotes and candles from your MetaTrader 5 terminal and
sends them to the V2 server. It never calls an order function (a test in backend/tests checks this file).

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


def bars_payload(rates, offset_hours: float) -> list[dict]:
    return [{"time": to_utc_iso(float(r["time"]), offset_hours), "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["tick_volume"])} for r in rates]


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
                 log=print):
        self.mt5, self.symbol, self.as_symbol = mt5, symbol, as_symbol.upper()
        self.url = server.rstrip("/") + "/api/bridge/push"
        self.token, self.offset, self.history, self.interval = token, utc_offset, history, interval
        self.post, self.clock, self.sleep, self.log = post, clock, sleep, log
        self.last_bar_time: float | None = None

    def connect(self) -> None:
        if not self.mt5.initialize():
            raise RuntimeError(f"cannot attach to the MetaTrader 5 terminal: {self.mt5.last_error()}")
        if not self.mt5.symbol_select(self.symbol, True):
            raise RuntimeError(f"symbol {self.symbol!r} not available in this terminal (check the broker's name for it)")
        if self.offset is None:
            tick = self.mt5.symbol_info_tick(self.symbol)
            self.offset = detect_offset_hours(tick.time_msc / 1000.0, self.clock()) if tick else None
            if self.offset is None:
                raise RuntimeError("cannot detect the broker's time offset (market closed?): pass --utc-offset")
        self.log(f"attached to MT5: {self.symbol} -> {self.as_symbol}, server time = UTC{self.offset:+g} h")

    def _send(self, tick=None, bars=None) -> dict:
        return self.post(self.url, self.token, {"symbol": self.as_symbol, "broker_symbol": self.symbol,
                                                "tick": tick, "bars_1m": bars or []})

    def send_history(self) -> None:
        # position 0 is the candle still forming: start at 1, closed candles only
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_M1, 1, self.history)
        if rates is None or not len(rates):
            raise RuntimeError(f"no 1-minute history for {self.symbol}: {self.mt5.last_error()}")
        bars = bars_payload(rates, self.offset)
        for i in range(0, len(bars), CHUNK):
            self._send(bars=bars[i:i + CHUNK])
        self.last_bar_time = float(rates[-1]["time"])
        self.log(f"sent {len(bars)} closed 1-minute candles, last {bars[-1]['time']}")

    def step(self) -> dict | None:
        tick = self.mt5.symbol_info_tick(self.symbol)
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_M1, 1, 5)
        new = [r for r in (rates if rates is not None else []) if self.last_bar_time is None
               or float(r["time"]) > self.last_bar_time]
        if new:
            self.last_bar_time = float(new[-1]["time"])
        if tick is None and not new:
            return None
        return self._send(tick_payload(tick, self.offset) if tick else None, bars_payload(new, self.offset))

    def run(self, max_steps: int | None = None) -> None:
        self.connect()
        self.send_history()
        n = 0
        while max_steps is None or n < max_steps:
            try:
                self.step()
            except Exception as e:                   # keep running through network or server hiccups
                self.log(f"push failed: {e}")
            n += 1
            self.sleep(self.interval)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Read-only MT5 price bridge for SPARKZ TRADER V2")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--as", dest="as_symbol", default="XAUUSD")
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--utc-offset", type=float, default=None)
    ap.add_argument("--history", type=int, default=20000)
    ap.add_argument("--interval", type=float, default=2.0)
    a = ap.parse_args(argv)
    token = os.getenv("SPARKZ_BRIDGE_TOKEN", "")
    if not token:
        sys.exit("set SPARKZ_BRIDGE_TOKEN in the environment (the same secret as the server)")
    try:
        import MetaTrader5 as mt5
    except ImportError:
        sys.exit("pip install MetaTrader5   (Windows only)")
    try:
        Bridge(mt5, a.symbol, a.as_symbol, a.server, token, a.utc_offset, a.history, a.interval).run()
    except KeyboardInterrupt:
        pass
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
