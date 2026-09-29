"""
OHLCV resampling (e.g. 1h -> 4h).

Yahoo Finance has no native 4h interval, so 4h candles are built here from
1h candles. Buckets are anchored to midnight UTC (00:00, 04:00, 08:00, ...),
the usual convention for FX 4h charts, and each bucket is labeled by its
OPEN time, matching how yfinance labels its own bars.

The one leakage trap here is the trailing bucket: if the newest 1h bar is
08:00, the 08:00-12:00 4h candle is still forming, and treating it as a
finished candle would hand downstream code a "close" that isn't final yet.
That trailing bucket is dropped unless the source data actually covers its
full span.
"""

from __future__ import annotations

import pandas as pd

from app.utils.time import timeframe_to_pandas_freq

REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def resample_ohlcv(
    df: pd.DataFrame,
    source_timeframe: str,
    target_timeframe: str,
    drop_incomplete_last: bool = True,
) -> pd.DataFrame:
    """
    Aggregate `df` (at `source_timeframe`) into `target_timeframe` candles:
    open=first, high=max, low=min, close=last, volume=sum.

    Buckets with no source bars at all (e.g. FX weekends) are omitted, not
    filled. Buckets that are only partly covered in the MIDDLE of the data
    (e.g. the Friday bucket that straddles the weekly close) are kept, since
    they are genuinely all the trading that happened in that window.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"resample_ohlcv: missing required columns: {missing}")

    source_delta = pd.Timedelta(timeframe_to_pandas_freq(source_timeframe))
    target_freq = timeframe_to_pandas_freq(target_timeframe)
    target_delta = pd.Timedelta(target_freq)
    if target_delta <= source_delta:
        raise ValueError(
            f"resample_ohlcv: target timeframe '{target_timeframe}' must be longer than "
            f"source timeframe '{source_timeframe}'."
        )
    if target_delta % source_delta != pd.Timedelta(0):
        raise ValueError(
            f"resample_ohlcv: target timeframe '{target_timeframe}' is not a whole multiple of "
            f"source timeframe '{source_timeframe}'."
        )

    if df.empty:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

    src = df[REQUIRED_COLUMNS].copy()
    src["timestamp"] = pd.to_datetime(src["timestamp"], utc=True)
    src = src.sort_values("timestamp").set_index("timestamp")

    out = (
        src.resample(target_freq, label="left", closed="left", origin="start_day")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )

    if drop_incomplete_last and not out.empty:
        last_source_close = src.index[-1] + source_delta
        last_bucket_close = out["timestamp"].iloc[-1] + target_delta
        if last_source_close < last_bucket_close:
            out = out.iloc[:-1]

    return out[REQUIRED_COLUMNS].reset_index(drop=True)
