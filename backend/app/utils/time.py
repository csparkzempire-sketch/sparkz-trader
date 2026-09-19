"""Time-related helpers used across the platform."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


TIMEFRAME_TO_PANDAS_FREQ = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
}


def timeframe_to_pandas_freq(timeframe: str) -> str:
    try:
        return TIMEFRAME_TO_PANDAS_FREQ[timeframe]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported timeframe '{timeframe}'. Supported: {sorted(TIMEFRAME_TO_PANDAS_FREQ)}"
        ) from exc
