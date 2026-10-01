"""Input validation shared by the CLI and the API."""

from __future__ import annotations

from app.config import TIMEFRAMES
from app.market.history import validate as validate_candles  # noqa: F401  (OHLC sanity checks)
from app.market.instruments import INSTRUMENTS


def check_symbol(symbol: str) -> str:
    s = symbol.upper()
    if s not in INSTRUMENTS:
        raise ValueError(f"unknown symbol {symbol!r}; supported: {', '.join(INSTRUMENTS)}")
    return s


def check_timeframe(tf: str) -> str:
    if tf not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {tf!r}; supported: {', '.join(TIMEFRAMES)}")
    return tf
