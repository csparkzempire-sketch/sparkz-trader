"""
Watched markets for the chart: candles of several symbols (gold, EURUSD, USDJPY, ...) pushed by the MT5 bridge
(`--watch`), kept in memory. Read-only market data: nothing here trades.

Each symbol keeps 5m / 15m / 1h / 4h / 1d candles. The last candle of each timeframe may still be forming
(`forming_since`); analysis uses closed candles only.
"""

from __future__ import annotations

import re
import threading
import time

import pandas as pd

WATCH_TFS = ("5m", "15m", "1h", "4h", "1d")
MAX_BARS = 3000
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def canonical(broker_symbol: str) -> str:
    """Broker name -> plain name: the upper-case head before a broker's suffix. XAUUSDm -> XAUUSD,
    EURUSD.raw -> EURUSD, BTCUSDm -> BTCUSD, USTECm -> USTEC, AAPLm -> AAPL; all lower case (usdjpy) -> USDJPY."""
    s = broker_symbol.strip()
    m = re.match(r"^([A-Z0-9]{2,})(?:[a-z.#_\-+!].*)?$", s)
    return (m.group(1) if m else s).upper()


def _tf_order(tf: str) -> int:
    return WATCH_TFS.index(tf) if tf in WATCH_TFS else len(WATCH_TFS)


class WatchStore:
    def __init__(self, clock=time.time):
        self.lock = threading.Lock()
        self.clock = clock
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.data: dict[str, dict] = {}

    def push(self, broker_symbol: str, tick: dict | None, bars: dict[str, list[dict]], digits: int | None = None) -> dict:
        name = canonical(broker_symbol)
        with self.lock:
            d = self.data.setdefault(name, {"broker_symbol": broker_symbol, "tick": None, "bars": {}, "updated": None,
                                            "forming": {}, "digits": None})
            d["broker_symbol"] = broker_symbol
            if digits is not None:
                d["digits"] = digits
            if tick is not None:
                d["tick"] = tick
            for tf, rows in bars.items():
                store = d["bars"].setdefault(tf, {})
                for b in rows:
                    store[pd.Timestamp(b["time"]).tz_convert("UTC")] = (
                        float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]), float(b.get("volume", 0.0)))
                if len(store) > MAX_BARS:
                    for k in sorted(store)[: len(store) - MAX_BARS]:
                        del store[k]
                forming = [b for b in rows if b.get("forming")]
                if forming:
                    d["forming"][tf] = pd.Timestamp(forming[-1]["time"]).tz_convert("UTC")
            d["updated"] = self.clock()
            return {"symbol": name, "timeframes": {tf: len(v) for tf, v in d["bars"].items()}}

    def symbols(self) -> list[dict]:
        with self.lock:
            now = self.clock()
            return [{"symbol": k, "broker_symbol": v["broker_symbol"], "timeframes": {tf: len(v["bars"][tf]) for tf in sorted(v["bars"], key=_tf_order)},
                     "age_seconds": None if v["updated"] is None else round(now - v["updated"], 1)}
                    for k, v in sorted(self.data.items())]

    def has(self, symbol: str) -> bool:
        return symbol.upper() in self.data

    def frame(self, symbol: str, tf: str, closed_only: bool = False) -> pd.DataFrame:
        with self.lock:
            d = self.data.get(symbol.upper())
            items = sorted((d["bars"].get(tf, {}) if d else {}).items())
            forming = d["forming"].get(tf) if d else None
        if closed_only and forming is not None:
            items = [(k, v) for k, v in items if k < forming]
        if not items:
            return pd.DataFrame(columns=COLUMNS)
        df = pd.DataFrame([(k, *v) for k, v in items], columns=COLUMNS)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df

    def tick(self, symbol: str) -> dict | None:
        with self.lock:
            d = self.data.get(symbol.upper())
            return dict(d["tick"]) if d and d["tick"] else None

    def digits(self, symbol: str) -> int | None:
        with self.lock:
            d = self.data.get(symbol.upper())
            return d["digits"] if d else None


WATCH = WatchStore()
