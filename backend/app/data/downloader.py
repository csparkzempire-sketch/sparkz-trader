"""
Market data downloader.

Uses yfinance for initial historical research data. This module ONLY
downloads and normalizes raw OHLCV data — validation and cleaning happen
in `validator.py`, and persistence happens in `repository.py`. Keeping
these separate makes each step independently testable.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.config import settings
from app.data.resample import resample_ohlcv
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
    "1d": "1d",
}

# Timeframes Yahoo doesn't serve natively: downloaded at the source timeframe
# and resampled (see app.data.resample). Yahoo's "4h" isn't a valid interval.
_RESAMPLED_TIMEFRAMES = {
    "4h": "1h",
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


# If a single long-period request comes back empty (Yahoo has tightened how
# much intraday history one request may return before), we retry in windows
# of this many days -- comfortably under the smallest limit we've seen work.
_CHUNK_DAYS = 55
_MAX_CONSECUTIVE_EMPTY_WINDOWS = 2


def _period_to_days(period: str | None) -> int | None:
    """'730d' -> 730, '5y' -> 1825, '3mo' -> 90; None if it isn't a simple period."""
    if not period:
        return None
    m = re.fullmatch(r"(\d+)(d|mo|y)", period.strip())
    if not m:
        return None
    return int(m.group(1)) * {"d": 1, "mo": 30, "y": 365}[m.group(2)]


def _download_in_chunks(yf, symbol: str, interval: str, lookback_days: int) -> pd.DataFrame | None:
    """
    Fetch `lookback_days` of history as a series of short start/end windows,
    walking backwards from now, and stitch whatever succeeds together.

    Stops early after a couple of consecutive empty windows -- that's what
    hitting Yahoo's "no intraday data this far back" limit looks like, and
    there's no point hammering it further. Returns None if nothing at all
    came back. Whatever was collected is still returned even if older
    windows failed, so a caller gets the most history Yahoo will give.
    """
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=1)  # yfinance's `end` is exclusive; +1d keeps today's bars
    covered = 0
    empty_streak = 0
    frames: list[pd.DataFrame] = []
    windows_tried = 0
    windows_ok = 0

    while covered < lookback_days:
        span = min(_CHUNK_DAYS, lookback_days - covered)
        window_start = window_end - timedelta(days=span)
        windows_tried += 1
        try:
            raw = yf.download(
                tickers=symbol,
                interval=interval,
                start=window_start.strftime("%Y-%m-%d"),
                end=window_end.strftime("%Y-%m-%d"),
                auto_adjust=False,
                progress=False,
            )
        except Exception as exc:  # broad on purpose: yfinance raises many things
            logger.warning("Chunk download failed %s", kv(symbol=symbol, error=str(exc)))
            raw = None

        if raw is None or raw.empty:
            empty_streak += 1
            if empty_streak >= _MAX_CONSECUTIVE_EMPTY_WINDOWS:
                break
        else:
            empty_streak = 0
            windows_ok += 1
            frames.append(_normalize_columns(raw))

        window_end = window_start
        covered += span

    if not frames:
        return None

    df = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset="timestamp", keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    logger.info(
        "Chunked download %s",
        kv(symbol=symbol, windows_tried=windows_tried, windows_ok=windows_ok, rows=len(df),
           first=str(df["timestamp"].iloc[0]), last=str(df["timestamp"].iloc[-1])),
    )
    return df


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
    Timeframes Yahoo lacks (4h) are downloaded at 1h and resampled, with any
    still-forming trailing candle dropped.

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

    if timeframe in _RESAMPLED_TIMEFRAMES:
        source_timeframe = _RESAMPLED_TIMEFRAMES[timeframe]
        source = download_ohlcv(symbol=symbol, timeframe=source_timeframe, period=period, start=start, end=end)
        resampled = resample_ohlcv(source, source_timeframe, timeframe)
        if resampled.empty:
            raise DownloadError(
                f"Not enough {source_timeframe} data for symbol={symbol!r} to build even one complete "
                f"{timeframe} candle."
            )
        logger.info(
            "Resampled download %s",
            kv(symbol=symbol, source_timeframe=source_timeframe, timeframe=timeframe,
               source_rows=len(source), rows=len(resampled)),
        )
        return resampled

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

    if (raw is None or raw.empty) and start is None and end is None and interval != "1d":
        lookback_days = _period_to_days(period)
        if lookback_days and lookback_days > _CHUNK_DAYS:
            logger.warning(
                "Period request returned no data; retrying in short windows %s",
                kv(symbol=symbol, period=period, chunk_days=_CHUNK_DAYS),
            )
            chunked = _download_in_chunks(yf, symbol, interval, lookback_days)
            if chunked is not None:
                logger.info("Download complete %s", kv(symbol=symbol, rows=len(chunked), via="chunked"))
                return chunked

    if raw is None or raw.empty:
        raise DownloadError(
            f"No data returned for symbol={symbol!r} timeframe={timeframe!r}. "
            "Check the symbol, timeframe, and date range."
        )

    df = _normalize_columns(raw)
    logger.info("Download complete %s", kv(symbol=symbol, rows=len(df)))
    return df
