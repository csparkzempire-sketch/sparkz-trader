"""
Dukascopy historical candles: free bid/ask history, no account, timestamps in UTC.

Two ways in, both giving 1-minute MID candles plus the bid/ask spread of each minute's close:

  download   one LZMA-compressed file per day and side from the public data feed
             https://datafeed.dukascopy.com/datafeed/<SYMBOL>/<YYYY>/<MM, 0-based>/<DD>/<BID|ASK>_candles_min_1.bi5
             (records of 24 bytes, big-endian: seconds into the day, open, close, low, high as integers
             in 1/DECIMAL_FACTOR price units, then a float volume)
  import     the CSV files the Dukascopy website exports ("Historical Data Feed": one file for BID, one
             for ASK, 1-minute interval)

Minutes where neither side traded ("flat" filler candles with zero volume, e.g. weekends) are dropped.
Read-only market data: nothing here can reach an account or an order.
"""

from __future__ import annotations

import lzma
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.market.providers.base import ProviderError

FEED = "https://datafeed.dukascopy.com/datafeed"
DECIMAL_FACTOR = {"XAUUSD": 1000, "EURUSD": 100_000, "GBPUSD": 100_000, "USDJPY": 1000}
PLAUSIBLE = {"XAUUSD": (500, 20_000), "EURUSD": (0.5, 2.5), "GBPUSD": (0.5, 3.0), "USDJPY": (50, 300)}
RECORD = struct.Struct(">IIIIIf")
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def _factor(symbol: str) -> int:
    if symbol.upper() not in DECIMAL_FACTOR:
        raise ValueError(f"no Dukascopy price scale for {symbol}; supported: {', '.join(DECIMAL_FACTOR)}")
    return DECIMAL_FACTOR[symbol.upper()]


def decode_day(raw: bytes, day: pd.Timestamp, factor: int) -> pd.DataFrame:
    """One day's bi5 file -> one side's 1m candles (all minutes, flats included)."""
    if not raw:
        return pd.DataFrame(columns=COLUMNS)
    data = lzma.decompress(raw)
    rows = [{"timestamp": day + pd.Timedelta(seconds=s), "open": o / factor, "high": h / factor,
             "low": lo / factor, "close": c / factor, "volume": float(v)}
            for s, o, c, lo, h, v in RECORD.iter_unpack(data[: len(data) - len(data) % RECORD.size])]
    return pd.DataFrame(rows, columns=COLUMNS)


def combine(bid: pd.DataFrame, ask: pd.DataFrame, symbol: str) -> tuple[pd.DataFrame, pd.Series]:
    """Bid and ask 1m candles -> mid candles and the close-to-close spread, flats dropped."""
    m = bid.merge(ask, on="timestamp", suffixes=("_b", "_a"))
    m = m[(m["volume_b"] > 0) | (m["volume_a"] > 0)].sort_values("timestamp").reset_index(drop=True)
    df = pd.DataFrame({"timestamp": pd.to_datetime(m["timestamp"], utc=True),
                       **{k: (m[f"{k}_b"] + m[f"{k}_a"]) / 2 for k in ("open", "high", "low", "close")},
                       "volume": m["volume_b"] + m["volume_a"]})
    lo, hi = PLAUSIBLE.get(symbol.upper(), (0, float("inf")))
    if len(df) and not lo <= df["close"].median() <= hi:
        raise ProviderError(f"Dukascopy {symbol} prices look mis-scaled (median {df['close'].median():.5g})")
    return df[COLUMNS], (m["close_a"] - m["close_b"]).reset_index(drop=True)


def spread_stats(spread: pd.Series) -> dict:
    return {"spread_median": float(spread.median()) if len(spread) else None,
            "spread_p90": float(spread.quantile(0.9)) if len(spread) else None,
            "spread_p99": float(spread.quantile(0.99)) if len(spread) else None}


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """1m candles -> a coarser timeframe (e.g. "15m"), bins labelled by their start, empty bins dropped."""
    if timeframe == "1m":
        return df
    rule = timeframe.replace("m", "min") if timeframe.endswith("m") else timeframe
    r = df.set_index("timestamp").resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["open"])
    return r.reset_index()[COLUMNS]


class _Throttle:
    """At most one feed request every `pause` seconds across all worker threads (the feed answers
    429 Too Many Requests to anything faster)."""

    def __init__(self, pause: float):
        self.pause, self.next, self.lock = pause, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next - now)
            self.next = max(now, self.next) + self.pause
        if delay:
            time.sleep(delay)


def _fetch_day(client, symbol: str, side: str, day: pd.Timestamp, throttle: _Throttle | None = None,
               cache_dir: Path | None = None, retries: int = 15) -> bytes:
    """One day's file. A cached copy (an empty file records "no data that day") is used when present,
    so an interrupted download resumes where it stopped. 429/5xx answers are retried with a growing
    pause of up to two minutes."""
    url = f"{FEED}/{symbol.upper()}/{day.year}/{day.month - 1:02d}/{day.day:02d}/{side}_candles_min_1.bi5"
    cached = cache_dir / symbol.upper() / f"{day:%Y-%m-%d}_{side}.bi5" if cache_dir else None
    if cached and cached.exists():
        return cached.read_bytes()
    for attempt in range(retries + 1):
        if throttle:
            throttle.wait()
        try:
            r = client.get(url)
        except Exception as exc:
            if attempt == retries:
                raise ProviderError(f"Dukascopy request failed: {type(exc).__name__}") from exc
        else:
            if r.status_code in (200, 404):
                content = r.content if r.status_code == 200 else b""      # 404: market closed that day
                if cached:
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    cached.write_bytes(content)
                return content
            if attempt == retries:
                raise ProviderError(f"Dukascopy returned HTTP {r.status_code} for {symbol} {side} {day:%Y-%m-%d}")
        time.sleep(min(120, 5 * 2 ** attempt))
    return b""


def download_1m(symbol: str, start, end=None, client=None, workers: int = 1, pause: float = 1.5,
                cache_dir: Path | None = None, progress=None) -> tuple[pd.DataFrame, dict]:
    """1m mid candles for every complete UTC day from `start` to `end` (default: yesterday)."""
    factor = _factor(symbol)
    start = pd.Timestamp(start, tz="UTC") if pd.Timestamp(start).tzinfo is None else pd.Timestamp(start)
    today = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
    end = min(pd.Timestamp(end, tz="UTC") if end and pd.Timestamp(end).tzinfo is None
              else (pd.Timestamp(end) if end else today), today)
    days = list(pd.date_range(start.normalize(), end - pd.Timedelta(days=1), freq="D", tz="UTC"))
    if client is None:
        import httpx

        client = httpx.Client(timeout=30.0)
    throttle = _Throttle(pause) if pause else None

    def one(day):
        bid, ask = (decode_day(_fetch_day(client, symbol, s, day, throttle, cache_dir), day, factor)
                    for s in ("BID", "ASK"))
        return combine(bid, ask, symbol) if len(bid) and len(ask) else None

    frames, spreads = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, res in enumerate(pool.map(one, days), 1):
            if res is not None and len(res[0]):
                frames.append(res[0])
                spreads.append(res[1])
            if progress:
                progress(i, days[i - 1])
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    sp = pd.concat(spreads, ignore_index=True) if spreads else pd.Series(dtype=float)
    return df, {"days_requested": len(days), "days_with_data": len(frames), "candles": len(df), **spread_stats(sp)}


def read_export(file: str | Path) -> pd.DataFrame:
    """One Dukascopy website CSV export ("Gmt time" or "Local time", Open, High, Low, Close, Volume)."""
    raw = pd.read_csv(file)
    raw.columns = [c.strip().lower() for c in raw.columns]
    col = next(c for c in raw.columns if c in ("gmt time", "local time", "time (utc)", "time"))
    txt = raw[col].astype(str).str.strip()
    if txt.str.contains("GMT").any():                       # "02.01.2024 00:00:00.000 GMT+0100"
        ts = pd.to_datetime(txt.str.replace(r"GMT([+-]\d{4})", r"\1", regex=True),
                            format="%d.%m.%Y %H:%M:%S.%f %z", utc=True)
    else:
        ts = pd.to_datetime(txt, dayfirst=True, utc=True)
    return pd.DataFrame({"timestamp": ts, **{k: raw[k].astype(float) for k in ("open", "high", "low", "close")},
                         "volume": raw["volume"].astype(float) if "volume" in raw else 0.0})[COLUMNS]


def import_exports(bid_file, ask_file, symbol: str) -> tuple[pd.DataFrame, dict]:
    """Website BID + ASK 1-minute exports -> 1m mid candles and spread stats."""
    bid, ask = read_export(bid_file), read_export(ask_file)
    gap = bid["timestamp"].diff().median()
    if len(bid) > 1 and gap != pd.Timedelta("1min"):
        raise ValueError(f"expected 1-minute Dukascopy exports, got a {gap} interval")
    df, sp = combine(bid, ask, symbol)
    return df, {"candles": len(df), "bid_rows": len(bid), "ask_rows": len(ask), **spread_stats(sp)}
