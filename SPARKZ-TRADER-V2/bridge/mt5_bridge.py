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
HTF = {"1h": ("TIMEFRAME_H1", 2000), "4h": ("TIMEFRAME_H4", 1000), "1d": ("TIMEFRAME_D1", 600)}
HTF_EVERY = 15 * 60          # seconds between higher-timeframe refreshes


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
                 log=print):
        self.mt5, self.symbol, self.as_symbol = mt5, symbol, as_symbol.upper()
        self.url = server.rstrip("/") + "/api/bridge/push"
        self.token, self.offset, self.history, self.interval = token, utc_offset, history, interval
        self.post, self.clock, self.sleep, self.log = post, clock, sleep, log
        self.last_bar_time: float | None = None
        self.point: float | None = None
        self.htf_sent: float | None = None

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
        return self.post(self.url, self.token, {"symbol": self.as_symbol, "broker_symbol": self.symbol,
                                                "tick": tick, "bars_1m": bars or []})

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

    def step(self) -> dict | None:
        if self.htf_sent is not None and self.clock() - self.htf_sent >= HTF_EVERY:
            self.send_higher_timeframes()
        tick = self.mt5.symbol_info_tick(self.symbol)
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_M1, 1, 5)
        new = [r for r in (rates if rates is not None else []) if self.last_bar_time is None
               or float(r["time"]) > self.last_bar_time]
        if new:
            self.last_bar_time = float(new[-1]["time"])
        if tick is None and not new:
            return None
        return self._send(tick_payload(tick, self.offset) if tick else None, bars_payload(new, self.offset, self.point))

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
