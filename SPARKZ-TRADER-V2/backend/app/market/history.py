"""
Stored historical candles: download, CSV import, validation and merge.

Files: data/candles/<SYMBOL>_<tf>.csv.gz. Every download is MERGED into the
file (history only grows; on duplicate timestamps the newer download wins), so
short-history intervals like 15m accumulate over time.

Validation (also applied to imports): UTC timestamps, sorted, de-duplicated,
consistent OHLC (high >= max(open, close), low <= min(open, close), positive
prices); invalid rows are dropped and counted.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.config import DATA_DIR
from app.market.instruments import get_instrument

CANDLES_DIR = DATA_DIR / "candles"
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


SOURCES = ("yahoo", "oanda")


def path_for(symbol: str, timeframe: str, source: str = "yahoo") -> Path:
    """Each data source has its own store: Yahoo's gold is the GC=F future, OANDA's is spot XAU_USD, and
    the two trade at different price levels, so they must never be merged into one series."""
    if source not in SOURCES:
        raise ValueError(f"unknown data source {source!r}; choose from {', '.join(SOURCES)}")
    base = CANDLES_DIR if source == "yahoo" else CANDLES_DIR / source      # yahoo keeps its original location
    return base / f"{symbol.upper()}_{timeframe}.csv.gz"


def validate(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if "volume" not in df:
        df["volume"] = 0.0
    n0 = len(df)
    df = df.dropna(subset=["open", "high", "low", "close"])
    ok = (df[["open", "high", "low", "close"]] > 0).all(axis=1) \
        & (df["high"] >= df[["open", "close"]].max(axis=1)) & (df["low"] <= df[["open", "close"]].min(axis=1))
    bad = int((~ok).sum())
    df = df[ok].sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    return df[COLUMNS], {"rows_in": n0, "rows_out": len(df), "invalid_ohlc": bad}


def load_history(symbol: str, timeframe: str, source: str = "yahoo") -> pd.DataFrame:
    """Stored candles for one data source. Yahoo candles are merged with the daily Yahoo archive
    (data/archive/, see market/archive.py)."""
    from app.market.archive import load_archive

    p = path_for(symbol, timeframe, source)
    parts = []
    if p.exists():
        df = pd.read_csv(p)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        parts.append(df)
    if source == "yahoo":
        arch = load_archive(symbol, timeframe)
        if len(arch):
            parts.append(arch)
    if not parts:
        hint = f" --source {source} --start YYYY-MM-DD" if source != "yahoo" else ""
        raise FileNotFoundError(f"no stored {source} candles for {symbol} {timeframe}; run "
                                f"`python -m app.cli download --symbol {symbol} --timeframe {timeframe}{hint}`")
    if len(parts) == 1:
        return parts[0]
    return validate(pd.concat(parts, ignore_index=True))[0]


def merge_into_store(new: pd.DataFrame, symbol: str, timeframe: str,
                     source: str = "yahoo") -> tuple[pd.DataFrame, dict]:
    new, report = validate(new)
    p = path_for(symbol, timeframe, source)
    if p.exists():
        old = pd.read_csv(p)
        old["timestamp"] = pd.to_datetime(old["timestamp"], utc=True)
        new, _ = validate(pd.concat([old, new], ignore_index=True))
        report["previously_stored"] = len(old)
    p.parent.mkdir(parents=True, exist_ok=True)
    new.to_csv(p, index=False, compression="gzip")
    report["stored"] = len(new)
    return new, report


def download(symbol: str, timeframe: str, source: str = "yahoo", start: str | None = None,
             end: str | None = None, progress=None) -> tuple[pd.DataFrame, dict]:
    """source "yahoo": the longest window Yahoo serves. source "oanda": broker history from `start`
    (read-only OANDA API; needs OANDA_API_TOKEN / OANDA_ACCOUNT_ID in the environment)."""
    get_instrument(symbol)
    if source == "oanda":
        from app.market.providers.broker_provider import BrokerProvider

        if not start:
            raise ValueError("an OANDA download needs --start (e.g. 2024-01-01)")
        df, stats = BrokerProvider(symbol, timeframe).get_history(timeframe, pd.Timestamp(start, tz="UTC"),
                                                                  pd.Timestamp(end, tz="UTC") if end else None,
                                                                  progress=progress)
        out, report = merge_into_store(df, symbol, timeframe, "oanda")
        return out, {**report, "source": "oanda", **stats}
    from app.market.providers.yahoo_provider import YahooProvider

    df = YahooProvider(symbol, timeframe).get_candles(timeframe, 1_000_000)
    out, report = merge_into_store(df, symbol, timeframe)
    return out, {**report, "source": "yahoo"}


def import_csv(file: str | Path, symbol: str, timeframe: str, tz: str = "UTC") -> tuple[pd.DataFrame, dict]:
    """Generic OHLC CSV (MT5 exports included): needs time/date+time, open, high, low, close columns."""
    raw = pd.read_csv(file, sep=None, engine="python")
    raw.columns = [c.strip("<>").lower() for c in raw.columns]
    if "date" in raw and "time" in raw:
        ts = pd.to_datetime(raw["date"].astype(str) + " " + raw["time"].astype(str))
    else:
        ts = pd.to_datetime(raw[next(c for c in raw.columns if c in ("timestamp", "datetime", "time", "date"))])
    ts = ts.dt.tz_localize(tz) if ts.dt.tz is None else ts
    df = pd.DataFrame({"timestamp": ts.dt.tz_convert("UTC"), "open": raw["open"], "high": raw["high"],
                       "low": raw["low"], "close": raw["close"],
                       "volume": raw.get("tickvol", raw.get("volume", 0.0))})
    return merge_into_store(df, symbol, timeframe)
