"""Dukascopy history (fake feed): bi5 decoding, mid + spread, flats dropped, 1m and 15m stores, CSV exports."""

import lzma
import struct

import pandas as pd
import pytest

from app.market import dukascopy
from app.market.providers.base import ProviderError

DAY = pd.Timestamp("2025-01-06", tz="UTC")             # a Monday


def bi5(side: str, minutes=60, flat_from=None) -> bytes:
    """One day's file: price 2600.000 + 0.01 per minute, ask 0.30 above bid; zero volume from `flat_from`."""
    out = b""
    for i in range(minutes):
        p = 2_600_000 + i * 10 + (300 if side == "ASK" else 0)            # 1/1000 price units
        vol = 0.0 if flat_from is not None and i >= flat_from else 1.5
        out += struct.pack(">IIIIIf", i * 60, p, p + 5, p - 20, p + 40, vol)   # open, close, low, high
    return lzma.compress(out, format=lzma.FORMAT_ALONE)


class Resp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


class FakeFeed:
    """Serves 2025-01-06 (with 10 flat minutes at the end) and 404s every other day."""

    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if "/2025/00/06/" in url:
            return Resp(200, bi5("ASK" if "ASK_" in url else "BID", minutes=60, flat_from=50))
        return Resp(404)


def test_throttled_requests_are_retried_and_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(dukascopy.time, "sleep", lambda s: None)

    class Busy(FakeFeed):
        def __init__(self):
            super().__init__()
            self.refused = 0

        def get(self, url):
            if self.refused < 3:
                self.refused += 1
                return Resp(429)
            return super().get(url)

    feed = Busy()
    df, _ = dukascopy.download_1m("XAUUSD", "2025-01-06", "2025-01-08", client=feed, workers=1,
                                  pause=0, cache_dir=tmp_path)
    assert len(df) == 50 and feed.refused == 3
    n = len(feed.urls)
    again, _ = dukascopy.download_1m("XAUUSD", "2025-01-06", "2025-01-08", client=feed, workers=1,
                                     pause=0, cache_dir=tmp_path)
    assert len(again) == 50 and len(feed.urls) == n             # second run served from the cache (404s too)


@pytest.fixture
def history(tmp_path, monkeypatch):
    from app.market import history
    monkeypatch.setattr(history, "CANDLES_DIR", tmp_path / "candles")
    return history


def test_decode_and_combine_to_mid_with_spread():
    df, stats = dukascopy.download_1m("XAUUSD", "2025-01-05", "2025-01-08", client=FakeFeed(), workers=2)
    assert stats["days_requested"] == 3 and stats["days_with_data"] == 1
    assert len(df) == 50                                       # 10 flat minutes dropped
    assert df["timestamp"].iloc[0] == DAY and df["timestamp"].is_monotonic_increasing
    assert df["open"].iloc[0] == pytest.approx(2600.15)       # mid of 2600.000 / 2600.300
    assert df["high"].iloc[0] == pytest.approx(2600.19) and df["low"].iloc[0] == pytest.approx(2600.13)
    assert stats["spread_median"] == pytest.approx(0.30)


def test_month_in_url_is_zero_based():
    feed = FakeFeed()
    dukascopy.download_1m("XAUUSD", "2025-01-06", "2025-01-07", client=feed, workers=1)
    assert sorted(feed.urls) == [f"{dukascopy.FEED}/XAUUSD/2025/00/06/ASK_candles_min_1.bi5",
                                 f"{dukascopy.FEED}/XAUUSD/2025/00/06/BID_candles_min_1.bi5"]


def test_http_error_is_reported_without_crashing_silently(monkeypatch):
    monkeypatch.setattr(dukascopy.time, "sleep", lambda s: None)

    class Down:
        def get(self, url):
            return Resp(503)

    with pytest.raises(ProviderError, match="HTTP 503"):
        dukascopy.download_1m("XAUUSD", "2025-01-06", "2025-01-07", client=Down(), workers=1)


def test_misscaled_prices_are_rejected():
    bid = dukascopy.decode_day(bi5("BID"), DAY, 1)               # wrong factor: prices 1000x too big
    ask = dukascopy.decode_day(bi5("ASK"), DAY, 1)
    with pytest.raises(ProviderError, match="mis-scaled"):
        dukascopy.combine(bid, ask, "XAUUSD")


def test_download_stores_1m_and_15m(history, monkeypatch):
    real = dukascopy.download_1m
    monkeypatch.setattr(dukascopy, "download_1m", lambda *a, **k: real(*a, client=FakeFeed(), workers=1, pause=0,
                                                                       cache_dir=None))
    out, rep = history.download("XAUUSD", "15m", "dukascopy", "2025-01-06", "2025-01-07")
    assert rep["stored"] == 50 and rep["stored_15m"] == 4 and rep["source"] == "dukascopy"
    m15 = history.load_history("XAUUSD", "15m", "dukascopy")
    assert len(m15) == 4 and m15["timestamp"].iloc[1] == DAY + pd.Timedelta("15min")
    one = history.load_history("XAUUSD", "1m", "dukascopy")
    assert m15["high"].iloc[0] == pytest.approx(one["high"].iloc[:15].max())
    assert m15["close"].iloc[0] == pytest.approx(one["close"].iloc[14])


def test_download_requires_start():
    from app.market.history import download
    with pytest.raises(ValueError):
        download("XAUUSD", "1m", source="dukascopy")


def export(path, side, local=False):
    head = "Local time" if local else "Gmt time"
    rows = []
    for i in range(30):
        t = DAY + pd.Timedelta(minutes=i)
        ts = (t + pd.Timedelta(hours=1)).strftime("%d.%m.%Y %H:%M:%S.000") + " GMT+0100" if local \
            else t.strftime("%d.%m.%Y %H:%M:%S.000")
        p = 2600 + i * 0.01 + (0.25 if side == "ask" else 0)
        rows.append(f"{ts},{p:.3f},{p + 0.04:.3f},{p - 0.02:.3f},{p + 0.005:.3f},{0 if i >= 25 else 1.2}")
    path.write_text(f"{head},Open,High,Low,Close,Volume\n" + "\n".join(rows) + "\n")
    return path


@pytest.mark.parametrize("local", [False, True])
def test_website_exports_import(tmp_path, history, local):
    bid, ask = export(tmp_path / "bid.csv", "bid", local), export(tmp_path / "ask.csv", "ask", local)
    out, rep = history.import_dukascopy(bid, ask, "XAUUSD", "15m")
    assert rep["candles"] == 25 and rep["spread_median"] == pytest.approx(0.25)
    one = history.load_history("XAUUSD", "1m", "dukascopy")
    assert one["timestamp"].iloc[0] == DAY                       # GMT+0100 local time -> UTC
    assert len(out) == 2                                          # 15m store


def test_export_must_be_one_minute(tmp_path):
    f = tmp_path / "h.csv"
    f.write_text("Gmt time,Open,High,Low,Close,Volume\n06.01.2025 00:00:00.000,1,1,1,1,1\n"
                 "06.01.2025 01:00:00.000,1,1,1,1,1\n06.01.2025 02:00:00.000,1,1,1,1,1\n")
    with pytest.raises(ValueError, match="1-minute"):
        dukascopy.import_exports(f, f, "XAUUSD")
