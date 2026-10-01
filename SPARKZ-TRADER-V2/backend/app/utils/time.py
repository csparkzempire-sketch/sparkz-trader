"""UTC time helpers. Candles are labelled by their OPEN time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import TIMEFRAMES


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def bar_open(t: datetime, timeframe: str) -> datetime:
    """Open time of the candle containing `t`."""
    step = TIMEFRAMES[timeframe]
    return datetime.fromtimestamp(int(t.timestamp()) // step * step, tz=timezone.utc)


def bar_close(open_time: datetime, timeframe: str) -> datetime:
    return open_time + timedelta(seconds=TIMEFRAMES[timeframe])


def is_closed(open_time: datetime, timeframe: str, now: datetime | None = None) -> bool:
    return bar_close(open_time, timeframe) <= (now or utcnow())
