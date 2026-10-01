"""
Daily candle archive: builds a long fine-grained history from Yahoo's short rolling windows.

Yahoo serves 1-minute candles for only the last 7 days and 5-minute candles for the last 60. Run
`collect` once a day and every COMPLETED UTC day is saved as its own file, written once and never
rewritten. The oldest day of each download is skipped because the window can start mid-day:

  data/archive/<SYMBOL>/<tf>/<YYYY-MM-DD>.csv.gz

One small file per day keeps the git history of the `market-data` branch small: each day adds
about 25 kB instead of rewriting one ever-growing file.

`load_archive` reads them back. `market.history.load_history` merges the archive into the stored
candles, so backtests can use it, e.g. as the intrabar path (`path_candles`).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from app.config import DATA_DIR
from app.market.instruments import get_instrument

ARCHIVE_DIR = Path(os.getenv("SPARKZ_V2_ARCHIVE_DIR") or DATA_DIR / "archive")
WINDOWS = {"1m": ("1m", "7d"), "5m": ("5m", "60d")}     # Yahoo's longest lookback per interval
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def day_path(symbol: str, timeframe: str, day: date, root: Path | None = None) -> Path:
    return (root or ARCHIVE_DIR) / symbol.upper() / timeframe / f"{day.isoformat()}.csv.gz"


def collect(symbol: str = "XAUUSD", timeframes: tuple[str, ...] = ("1m", "5m"), root: Path | None = None,
            downloader=None, today: date | None = None) -> dict:
    """Download the recent window for each timeframe and save every completed UTC day not yet archived.
    Returns {timeframe: {"written": [...days], "skipped_existing": n, "error": str|None}}."""
    from app.market.history import validate
    from app.market.providers.yahoo_provider import _download

    dl = downloader or _download
    inst = get_instrument(symbol)
    today = today or datetime.now(timezone.utc).date()
    out: dict = {}
    for tf in timeframes:
        if tf not in WINDOWS:
            raise ValueError(f"archive supports {', '.join(WINDOWS)}, not {tf}")
        res = {"written": [], "skipped_existing": 0, "error": None}
        out[tf] = res
        try:
            df, _ = validate(dl(inst.yahoo, *WINDOWS[tf]))
        except Exception as e:      # one failing timeframe must not stop the others
            res["error"] = f"{type(e).__name__}: {e}"
            continue
        days = df["timestamp"].dt.date
        first = days.min() if len(days) else None
        for d, g in df.groupby(days):
            if d >= today:          # the current UTC day is still forming
                continue
            if d == first:          # Yahoo's window starts mid-day: the oldest day may be partial
                continue
            p = day_path(symbol, tf, d, root)
            if p.exists():
                res["skipped_existing"] += 1
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            g[COLUMNS].to_csv(tmp, index=False, compression="gzip")
            tmp.replace(p)          # atomic: a crash never leaves a half-written day
            res["written"].append(d.isoformat())
    return out


def archived_days(symbol: str, timeframe: str, root: Path | None = None) -> list[str]:
    d = (root or ARCHIVE_DIR) / symbol.upper() / timeframe
    return sorted(p.name.removesuffix(".csv.gz") for p in d.glob("*.csv.gz")) if d.exists() else []


def load_archive(symbol: str, timeframe: str, start: str | None = None, end: str | None = None,
                 root: Path | None = None) -> pd.DataFrame:
    """All archived candles for symbol/timeframe, optionally limited to days start..end (YYYY-MM-DD)."""
    days = [d for d in archived_days(symbol, timeframe, root) if (not start or d >= start) and (not end or d <= end)]
    if not days:
        return pd.DataFrame(columns=COLUMNS)
    parts = [pd.read_csv(day_path(symbol, timeframe, date.fromisoformat(d), root)) for d in days]
    df = pd.concat(parts, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def status(symbol: str = "XAUUSD", root: Path | None = None) -> dict:
    out = {}
    for tf in WINDOWS:
        days = archived_days(symbol, tf, root)
        out[tf] = {"days": len(days), "first": days[0] if days else None, "last": days[-1] if days else None}
    return out
