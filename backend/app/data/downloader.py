"""
Market data downloader.

Uses yfinance for initial historical research data. This module ONLY
downloads and normalizes raw OHLCV data — validation and cleaning happen
in `validator.py`, and persistence happens in `repository.py`. Keeping
these separate makes each step independently testable.
"""

from __future__ import annotations

import pandas as pd

from app.config import settings
from app.utils.logging import get_logger, kv

logger = get_logger(__name__, settings.log_level)

REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

# yfinance interval strings differ slightly from our internal timeframe names.
_TIMEFRAME_TO_YF_INTERVAL = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "4h": "4h",  # not natively supported by yfinance; resample from 1h upstream if needed
    "1d": "1d",
}

# yfinance limits how far back intraday data goes depending on interval.
_MAX_PERIOD_FOR_INTRADAY = {
    "1m": "7d",
    "5m": "60d",
    "15m": "60d",
    "30m": "60d",
    "60m": "730d",
}


class DownloadError(RuntimeError):
    """Raised when market data cannot be downloaded or is unusable."""


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize yfinance's column names/index into our canonical schema."""
    df = df.copy()

    # yfinance sometimes returns a MultiIndex column set (Adj Close, etc.)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    df = df.reset_index()
    # The index column is named "Datetime" for intraday, "Date" for daily.
    rename_map = {
        "Datetime": "timestamp",
        "Date": "timestamp",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    missing = [c for c in ["timestamp", "open", "high", "low", "close"] if c not in df.columns]
    if missing:
        raise DownloadError(f"Downloaded data is missing required columns: {missing}")

    if "volume" not in df.columns:
        df["volume"] = 0.0

    return df[REQUIRED_COLUMNS]


def download_ohlcv(
    symbol: str | None = None,
    timeframe: str | None = None,
    period: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """
    Download historical OHLCV data for `symbol` at the given `timeframe`.

    Either `period` (e.g. "60d") or `start`/`end` dates may be supplied.
    If neither is given, a sensible default period for the timeframe is used.

    Returns a DataFrame with columns: timestamp, open, high, low, close, volume.
    Raises DownloadError on failure (network error, empty result, unsupported
    timeframe, etc.) — callers must not silently swallow this.
    """
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - environment issue, not logic
        raise DownloadError(
            "yfinance is not installed. Add it to backend/requirements.txt and `pip install -r requirements.txt`."
        ) from exc

    symbol = symbol or settings.market_symbol
    timeframe = timeframe or settings.timeframe

    if timeframe not in _TIMEFRAME_TO_YF_INTERVAL:
        raise DownloadError(f"Unsupported timeframe '{timeframe}'.")

    interval = _TIMEFRAME_TO_YF_INTERVAL[timeframe]

    if period is None and start is None and end is None:
        period = _MAX_PERIOD_FOR_INTRADAY.get(interval, "5y")

    logger.info("Starting download %s", kv(symbol=symbol, timeframe=timeframe, interval=interval, period=period))

    try:
        raw = yf.download(
            tickers=symbol,
            interval=interval,
            period=period,
            start=start,
            end=end,
            auto_adjust=False,
            progress=False,
        )
    except Exception as exc:  # network/library errors from yfinance are broad
        raise DownloadError(f"Failed to download data for {symbol}: {exc}") from exc

    if raw is None or raw.empty:
        raise DownloadError(
            f"No data returned for symbol={symbol!r} timeframe={timeframe!r}. "
            "Check the symbol, timeframe, and date range."
        )

    df = _normalize_columns(raw)
    logger.info("Download complete %s", kv(symbol=symbol, rows=len(df)))
    return df
