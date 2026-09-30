"""
Yahoo Finance downloads.

History Yahoo serves per interval: 15m and 30m about 60 days, 1h about 730
days, 1d decades. 4h is built by resampling 1h. See data/instruments.py for
the XAUUSD futures-proxy caveat.
"""

from __future__ import annotations

import pandas as pd

from app.data.instruments import get_instrument
from app.data.validator import closed_candles, validate

YAHOO: dict[str, tuple[str, str]] = {   # timeframe -> (yahoo interval, max period)
    "15m": ("15m", "60d"),
    "30m": ("30m", "60d"),
    "1h": ("1h", "730d"),
    "4h": ("1h", "730d"),
    "1d": ("1d", "max"),
}


class DownloadError(RuntimeError):
    pass


def _fetch(ticker: str, interval: str, period: str) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=False, threads=False)
    if raw is None or raw.empty:
        raise DownloadError(f"Yahoo returned no data for {ticker} {interval}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.reset_index()
    raw = raw.rename(columns={raw.columns[0]: "timestamp"})
    raw.columns = [str(c).lower() for c in raw.columns]
    return raw[["timestamp", "open", "high", "low", "close", "volume"]]


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = df.set_index("timestamp").resample(rule, label="left", closed="left")
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return out.reset_index()


def download(symbol: str, timeframe: str, fetch=_fetch) -> tuple[pd.DataFrame, dict]:
    """Closed, validated candles from Yahoo, timestamps converted to UTC."""
    inst = get_instrument(symbol)
    if timeframe not in YAHOO:
        raise ValueError(f"Unsupported timeframe {timeframe!r}")
    interval, period = YAHOO[timeframe]
    raw = fetch(inst.yahoo, interval, period)
    df, rep = validate(raw, "1h" if timeframe == "4h" else timeframe)
    if timeframe == "4h":
        df, rep = validate(resample(df, "4h"), "4h")
    df = closed_candles(df, timeframe)
    return df, {**rep.to_dict(), "source": f"yahoo:{inst.yahoo}", "note": inst.note}
