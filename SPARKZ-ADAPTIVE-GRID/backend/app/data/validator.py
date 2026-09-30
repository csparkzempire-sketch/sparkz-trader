"""
Market data validation.

Every data source (Yahoo download, CSV import, SQLite) passes through
`validate`, so the strategy only ever sees clean, UTC, sorted, de-duplicated,
internally consistent OHLC bars. Problems are counted in a report instead of
being silently fixed where fixing would invent data.

`closed_candles` removes bars that have not finished yet. Yahoo includes the
forming candle in intraday downloads; trading on it would use prices from the
future of that bar's close.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import pandas as pd

from app.utils.time import bar_length, utc_now

REQUIRED = ["timestamp", "open", "high", "low", "close"]


class DataValidationError(ValueError):
    pass


@dataclass
class ValidationReport:
    rows_in: int = 0
    rows_out: int = 0
    duplicates: int = 0
    missing_values: int = 0
    non_positive: int = 0
    inconsistent_ohlc: int = 0
    gaps: int = 0                  # jumps longer than one bar, excluding weekend closes
    first: str | None = None
    last: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def validate(df: pd.DataFrame, timeframe: str, min_rows: int = 1) -> tuple[pd.DataFrame, ValidationReport]:
    rep = ValidationReport(rows_in=len(df))
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise DataValidationError(f"missing columns: {missing}")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    keep = REQUIRED + ["volume"] + (["spread"] if "spread" in df.columns else [])
    df = df[keep]

    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["timestamp"] = ts
    n = len(df)
    df = df.dropna(subset=REQUIRED)
    rep.missing_values = n - len(df)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)

    n = len(df)
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    rep.non_positive = n - len(df)

    n = len(df)
    df = df.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    rep.duplicates = n - len(df)

    n = len(df)
    ok = (df["high"] >= df[["open", "close"]].max(axis=1)) & (df["low"] <= df[["open", "close"]].min(axis=1)) \
        & (df["high"] >= df["low"])
    df = df[ok]
    rep.inconsistent_ohlc = n - len(df)

    df = df.reset_index(drop=True)
    if len(df) > 1:
        step = df["timestamp"].diff()
        long = step > bar_length(timeframe) * 1.5
        # A market closed for the weekend (Fri/Sat -> Sun/Mon, under ~3 days) is not a data gap.
        prev_day = df["timestamp"].shift().dt.dayofweek
        weekend = long & (step < pd.Timedelta(days=3, hours=6)) & prev_day.isin([4, 5]) \
            & df["timestamp"].dt.dayofweek.isin([6, 0])
        rep.gaps = int((long & ~weekend).sum())
    rep.rows_out = len(df)
    if len(df) < min_rows:
        raise DataValidationError(f"only {len(df)} valid rows (need {min_rows})")
    if len(df):
        rep.first, rep.last = df["timestamp"].iloc[0].isoformat(), df["timestamp"].iloc[-1].isoformat()
    return df, rep


def closed_candles(df: pd.DataFrame, timeframe: str, now: datetime | None = None) -> pd.DataFrame:
    """Drop bars that haven't closed yet: a bar opened at t closes at t + bar length."""
    now = pd.Timestamp(now or utc_now())
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    return df[df["timestamp"] + bar_length(timeframe) <= now].reset_index(drop=True)
