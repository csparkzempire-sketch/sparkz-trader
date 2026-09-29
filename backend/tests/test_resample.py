"""Tests for app.data.resample (1h -> 4h candles)."""

from __future__ import annotations

import pandas as pd
import pytest

from app.data.downloader import download_ohlcv
from app.data.resample import resample_ohlcv


def _hourly(start: str, n: int) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    close = [1.0 + 0.01 * i for i in range(n)]
    return pd.DataFrame({
        "timestamp": ts,
        "open": [c - 0.005 for c in close],
        "high": [c + 0.02 for c in close],
        "low": [c - 0.02 for c in close],
        "close": close,
        "volume": [10.0] * n,
    })


def test_aggregates_ohlcv_into_midnight_anchored_buckets():
    df = _hourly("2024-01-01 00:00", 8)
    out = resample_ohlcv(df, "1h", "4h")

    assert list(out["timestamp"]) == [
        pd.Timestamp("2024-01-01 00:00", tz="UTC"),
        pd.Timestamp("2024-01-01 04:00", tz="UTC"),
    ]
    first = out.iloc[0]
    assert first["open"] == pytest.approx(df["open"].iloc[0])
    assert first["high"] == pytest.approx(df["high"].iloc[:4].max())
    assert first["low"] == pytest.approx(df["low"].iloc[:4].min())
    assert first["close"] == pytest.approx(df["close"].iloc[3])
    assert first["volume"] == pytest.approx(40.0)


def test_buckets_align_to_midnight_even_when_data_starts_mid_bucket():
    df = _hourly("2024-01-01 02:00", 10)  # 02:00 .. 11:00
    out = resample_ohlcv(df, "1h", "4h")
    assert all(ts.hour % 4 == 0 for ts in out["timestamp"])
    # 00:00 bucket (partial, at the START of the data) is kept; 08:00 bucket is complete.
    assert out["timestamp"].iloc[0] == pd.Timestamp("2024-01-01 00:00", tz="UTC")
    assert out["timestamp"].iloc[-1] == pd.Timestamp("2024-01-01 08:00", tz="UTC")


def test_still_forming_trailing_bucket_is_dropped():
    """Last 1h bar is 09:00 -> the 08:00-12:00 candle hasn't closed yet."""
    df = _hourly("2024-01-01 00:00", 10)
    out = resample_ohlcv(df, "1h", "4h")
    assert out["timestamp"].iloc[-1] == pd.Timestamp("2024-01-01 04:00", tz="UTC")

    kept = resample_ohlcv(df, "1h", "4h", drop_incomplete_last=False)
    assert kept["timestamp"].iloc[-1] == pd.Timestamp("2024-01-01 08:00", tz="UTC")


def test_empty_gaps_are_not_fabricated():
    """A weekend-style gap must not produce flat filler candles."""
    df = pd.concat([_hourly("2024-01-05 00:00", 8), _hourly("2024-01-07 00:00", 8)], ignore_index=True)
    out = resample_ohlcv(df, "1h", "4h")
    assert len(out) == 4
    assert out[["open", "high", "low", "close"]].notna().all().all()


def test_rejects_non_higher_or_non_multiple_timeframes():
    df = _hourly("2024-01-01", 8)
    with pytest.raises(ValueError):
        resample_ohlcv(df, "4h", "1h")
    with pytest.raises(ValueError):
        resample_ohlcv(df, "1h", "1h")


def test_download_4h_fetches_1h_and_resamples(monkeypatch):
    calls = []

    def fake_download(tickers, interval, period=None, start=None, end=None, **kw):
        calls.append(interval)
        idx = pd.date_range("2024-01-01", periods=48, freq="1h", tz="UTC", name="Datetime")
        return pd.DataFrame(
            {"Open": 1.1, "High": 1.11, "Low": 1.09, "Close": 1.1, "Adj Close": 1.1, "Volume": 0.0},
            index=idx,
        )

    monkeypatch.setattr("yfinance.download", fake_download)
    df = download_ohlcv("EURUSD=X", "4h")

    assert calls and all(c == "60m" for c in calls)  # never asks Yahoo for a "4h" interval
    assert len(df) == 12
    assert (df["timestamp"].diff().dropna() == pd.Timedelta("4h")).all()
