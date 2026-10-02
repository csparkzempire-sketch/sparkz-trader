"""OANDA history download (fake client): paging, closed candles only, mid prices, spread stats, own store."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from app.market.providers.broker_provider import BrokerProvider


class Resp:
    def __init__(self, data):
        self.status_code, self._d = 200, data

    def json(self):
        return self._d


class PagingClient:
    """Serves 1m bid/ask candles from a fixed series, at most `page` per request, honouring from/includeFirst."""

    def __init__(self, n=12_000, page=5000):
        self.times = pd.date_range("2025-01-06 00:00", periods=n, freq="1min", tz="UTC")
        self.page, self.calls = page, []

    def get(self, url, params=None, headers=None):
        self.calls.append(params)
        start = pd.Timestamp(params["from"])
        idx = self.times.searchsorted(start, side="left" if params["includeFirst"] == "true" else "right")
        out = []
        for i in range(idx, min(idx + min(params["count"], self.page), len(self.times))):
            p = 2000 + i * 0.01
            out.append({"time": self.times[i].isoformat(), "complete": i < len(self.times) - 1, "volume": 3,
                        "bid": {k: f"{p - 0.15:.2f}" for k in "ohlc"}, "ask": {k: f"{p + 0.15:.2f}" for k in "ohlc"}})
        return Resp({"candles": out})


def provider(client):
    return BrokerProvider("XAUUSD", "1m", token="t", account_id="a", client=client)


def test_history_pages_without_gaps_or_duplicates():
    c = PagingClient(n=12_000)
    df, stats = provider(c).get_history("1m", datetime(2025, 1, 6, tzinfo=timezone.utc),
                                        datetime(2025, 1, 20, tzinfo=timezone.utc))
    assert len(df) == 11_999                       # the last (forming) candle is excluded
    assert df["timestamp"].is_unique and df["timestamp"].is_monotonic_increasing
    assert (df["timestamp"].diff().dropna() == pd.Timedelta("1min")).all()
    assert stats["pages"] >= 3 and c.calls[0]["price"] == "BA" and c.calls[1]["includeFirst"] == "false"
    assert df["close"].iloc[0] == pytest.approx(2000.0)            # mid of bid/ask
    assert stats["spread_median"] == pytest.approx(0.30)


def test_history_stops_at_end_date():
    c = PagingClient(n=12_000)
    end = datetime(2025, 1, 6, 1, 0, tzinfo=timezone.utc)
    df, _ = provider(c).get_history("1m", datetime(2025, 1, 6, tzinfo=timezone.utc), end)
    assert len(df) == 60 and df["timestamp"].max() < pd.Timestamp(end)


def test_sources_have_separate_stores(tmp_path, monkeypatch):
    from app.market import history
    monkeypatch.setattr(history, "CANDLES_DIR", tmp_path)
    assert history.path_for("XAUUSD", "1m", "oanda") != history.path_for("XAUUSD", "1m", "yahoo")
    df = pd.DataFrame({"timestamp": pd.date_range("2025-01-06", periods=3, freq="1min", tz="UTC"),
                       "open": [1.0, 2, 3], "high": [1.0, 2, 3], "low": [1.0, 2, 3], "close": [1.0, 2, 3],
                       "volume": 0.0})
    history.merge_into_store(df, "XAUUSD", "1m", "oanda")
    assert len(history.load_history("XAUUSD", "1m", "oanda")) == 3
    with pytest.raises(FileNotFoundError):
        history.load_history("XAUUSD", "1m", "yahoo")
    with pytest.raises(ValueError):
        history.path_for("XAUUSD", "1m", "nosuchfeed")


def test_oanda_download_requires_start():
    from app.market.history import download
    with pytest.raises(ValueError):
        download("XAUUSD", "1m", source="oanda")
