"""Tests for app.data.downloader's period -> chunked-window fallback.

Background: on 2026-09-28 Yahoo Finance started returning NO data for
hourly requests of 300d or longer (60d still worked), although 730d had
worked days earlier -- yfinance reported it as "possibly delisted; no price
data found". These tests replace yfinance.download with a fake that
reproduces that behaviour so the fallback can be exercised without network.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.data import downloader
from app.data.downloader import DownloadError, _period_to_days, download_ohlcv


class FakeYahoo:
    """Stand-in for yfinance.download.

    - Requests with a `period` longer than `max_period_days` return nothing.
    - Requests with start/end return hourly bars, but only those no older
      than `max_history_days` (simulating "no intraday data this far back").
    """

    def __init__(self, max_period_days: int = 60, max_history_days: int = 400):
        self.max_period_days = max_period_days
        self.max_history_days = max_history_days
        self.calls: list[dict] = []

    def __call__(self, tickers, interval, period=None, start=None, end=None, **kwargs):
        self.calls.append({"period": period, "start": start, "end": end})
        now = pd.Timestamp.now(tz="UTC")

        if period is not None:
            days = downloader._period_to_days(period)
            if days is not None and days > self.max_period_days:
                return pd.DataFrame()
            begin, finish = now - pd.Timedelta(days=days or 5), now
        else:
            begin = pd.Timestamp(start, tz="UTC")
            finish = pd.Timestamp(end, tz="UTC")

        oldest_allowed = now - pd.Timedelta(days=self.max_history_days)
        begin = max(begin, oldest_allowed)
        finish = min(finish, now)
        if begin >= finish:
            return pd.DataFrame()

        idx = pd.date_range(begin.floor("h"), finish.floor("h"), freq="1h", inclusive="left", name="Datetime")
        if len(idx) == 0:
            return pd.DataFrame()
        n = len(idx)
        return pd.DataFrame(
            {"Open": 1.1, "High": 1.11, "Low": 1.09, "Close": 1.1, "Adj Close": 1.1, "Volume": 0.0},
            index=idx,
        ).iloc[:n]


@pytest.fixture
def fake_yahoo(monkeypatch):
    fake = FakeYahoo()
    monkeypatch.setattr("yfinance.download", fake)
    return fake


def test_period_to_days():
    assert _period_to_days("730d") == 730
    assert _period_to_days("5y") == 1825
    assert _period_to_days("3mo") == 90
    assert _period_to_days(None) is None
    assert _period_to_days("max") is None


def test_normal_request_does_not_chunk(monkeypatch):
    """If the long request works, behavior must be exactly as before: one call."""
    fake = FakeYahoo(max_period_days=1000)
    monkeypatch.setattr("yfinance.download", fake)
    df = download_ohlcv("EURUSD=X", "1h")
    assert len(fake.calls) == 1
    assert fake.calls[0]["period"] == "730d"
    assert len(df) > 0


def test_empty_long_period_falls_back_to_chunked_windows(fake_yahoo):
    """The exact production failure: 730d returns nothing -> retry in windows."""
    df = download_ohlcv("EURUSD=X", "1h")

    assert fake_yahoo.calls[0]["period"] == "730d"  # tried the normal way first
    assert len(fake_yahoo.calls) > 2  # ...then fell back to several windows
    assert all(c["period"] is None for c in fake_yahoo.calls[1:])  # windows use start/end

    # We got real, stitched history: sorted, no duplicate bars, well beyond
    # the 60 days a single short request could give.
    assert df["timestamp"].is_monotonic_increasing
    assert not df["timestamp"].duplicated().any()
    span_days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days
    assert span_days > 300
    assert list(df.columns) == downloader.REQUIRED_COLUMNS


def test_chunking_stops_after_consecutive_empty_windows(monkeypatch):
    """Hitting Yahoo's 'no intraday data this far back' wall must stop the
    walk, not keep hammering ~13 more windows; and the history that WAS
    reachable must still be returned."""
    fake = FakeYahoo(max_period_days=60, max_history_days=120)
    monkeypatch.setattr("yfinance.download", fake)
    df = download_ohlcv("EURUSD=X", "1h")

    windows = [c for c in fake.calls if c["period"] is None]
    assert len(windows) < 8  # 730d / 55d would be 14 windows if it never stopped
    span_days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days
    assert 60 < span_days <= 125


def test_nothing_works_still_raises_download_error(monkeypatch):
    monkeypatch.setattr("yfinance.download", lambda **kw: pd.DataFrame())
    with pytest.raises(DownloadError):
        download_ohlcv("EURUSD=X", "1h")


def test_explicit_start_end_request_is_never_chunked(monkeypatch):
    """A caller who asked for a specific range gets exactly that request --
    the period fallback must not silently substitute something else."""
    calls = []

    def empty(**kw):
        calls.append(kw)
        return pd.DataFrame()

    monkeypatch.setattr("yfinance.download", empty)
    with pytest.raises(DownloadError):
        download_ohlcv("EURUSD=X", "1h", start="2020-01-01", end="2020-02-01")
    assert len(calls) == 1


def test_daily_interval_is_not_chunked(monkeypatch):
    """Daily bars have no short-window limit problem; don't apply the fallback."""
    calls = []

    def empty(**kw):
        calls.append(kw)
        return pd.DataFrame()

    monkeypatch.setattr("yfinance.download", empty)
    with pytest.raises(DownloadError):
        download_ohlcv("EURUSD=X", "1d")
    assert len(calls) == 1
