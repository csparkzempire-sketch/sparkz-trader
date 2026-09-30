"""Time helpers. Everything internal is timezone-aware UTC."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

# Bar length per supported timeframe.
TIMEFRAMES: dict[str, pd.Timedelta] = {
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
    "1d": pd.Timedelta(days=1),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def bar_length(timeframe: str) -> pd.Timedelta:
    try:
        return TIMEFRAMES[timeframe]
    except KeyError:
        raise ValueError(f"Unsupported timeframe {timeframe!r}. Supported: {', '.join(TIMEFRAMES)}") from None


def bars_per_year(timeframe: str, symbol: str) -> float:
    """Approximate bars per year: crypto trades 24/7, FX and gold about 5 x 24h a week."""
    hours = 24 * 365 if symbol.upper().startswith("BTC") else 24 * 5 * 52
    return hours * 3600 / bar_length(timeframe).total_seconds()
