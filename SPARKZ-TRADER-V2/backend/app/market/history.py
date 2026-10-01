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


def path_for(symbol: str, timeframe: str) -> Path:
    return CANDLES_DIR / f"{symbol.upper()}_{timeframe}.csv.gz"


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


def load_history(symbol: str, timeframe: str) -> pd.DataFrame:
    """Stored candles merged with the daily archive (data/archive/, see market/archive.py)."""
    from app.market.archive import load_archive

    p = path_for(symbol, timeframe)
    parts = []
    if p.exists():
        df = pd.read_csv(p)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        parts.append(df)
    arch = load_archive(symbol, timeframe)
    if len(arch):
        parts.append(arch)
    if not parts:
        raise FileNotFoundError(f"no stored candles for {symbol} {timeframe}; run "
                                f"`python -m app.cli download --symbol {symbol} --timeframe {timeframe}`")
    if len(parts) == 1:
        return parts[0]
    return validate(pd.concat(parts, ignore_index=True))[0]


def merge_into_store(new: pd.DataFrame, symbol: str, timeframe: str) -> tuple[pd.DataFrame, dict]:
    new, report = validate(new)
    p = path_for(symbol, timeframe)
    if p.exists():
        old = load_history(symbol, timeframe)
        new, _ = validate(pd.concat([old, new], ignore_index=True))
        report["previously_stored"] = len(old)
    p.parent.mkdir(parents=True, exist_ok=True)
    new.to_csv(p, index=False, compression="gzip")
    report["stored"] = len(new)
    return new, report


def download(symbol: str, timeframe: str) -> tuple[pd.DataFrame, dict]:
    from app.market.providers.yahoo_provider import YahooProvider

    get_instrument(symbol)
    df = YahooProvider(symbol, timeframe).get_candles(timeframe, 1_000_000)
    return merge_into_store(df, symbol, timeframe)


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
