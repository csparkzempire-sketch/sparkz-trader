"""Daily candle archive: completed days only, write-once, merged into load_history."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.market import archive


def fake_dl(days=3, end="2026-10-01 12:00", freq="1min"):
    def dl(symbol, interval, period):
        ts = pd.date_range(end=pd.Timestamp(end, tz="UTC"), periods=days * 1440 // (1 if freq == "1min" else 5),
                           freq=freq)
        px = 2000 + np.cumsum(np.ones(len(ts)) * 0.01)
        return pd.DataFrame({"timestamp": ts, "open": px, "high": px + 0.5, "low": px - 0.5, "close": px,
                             "volume": 1.0})
    return dl


def test_collect_saves_only_completed_days_and_never_rewrites(tmp_path):
    res = archive.collect("XAUUSD", ("1m",), root=tmp_path, downloader=fake_dl(), today=date(2026, 10, 1))
    assert res["1m"]["error"] is None
    assert "2026-10-01" not in res["1m"]["written"]          # today is still forming
    assert res["1m"]["written"][-1] == "2026-09-30"
    assert "2026-09-28" not in res["1m"]["written"]          # oldest day of the window may be partial
    p = archive.day_path("XAUUSD", "1m", date(2026, 9, 30), tmp_path)
    before = p.read_bytes()
    again = archive.collect("XAUUSD", ("1m",), root=tmp_path, downloader=fake_dl(), today=date(2026, 10, 2))
    assert again["1m"]["written"] == ["2026-10-01"]
    assert p.read_bytes() == before                         # write-once
    assert again["1m"]["skipped_existing"] >= 1


def test_full_day_round_trips(tmp_path):
    archive.collect("XAUUSD", ("1m",), root=tmp_path, downloader=fake_dl(), today=date(2026, 10, 1))
    df = archive.load_archive("XAUUSD", "1m", start="2026-09-30", end="2026-09-30", root=tmp_path)
    assert len(df) == 1440 and df["timestamp"].dt.date.nunique() == 1
    assert df["timestamp"].is_monotonic_increasing and str(df["timestamp"].dt.tz) == "UTC"
    assert archive.status("XAUUSD", tmp_path)["1m"]["last"] == "2026-09-30"


def test_download_error_is_reported_per_timeframe(tmp_path):
    def boom(*a):
        raise RuntimeError("rate limited")
    res = archive.collect("XAUUSD", ("1m", "5m"), root=tmp_path, downloader=boom, today=date(2026, 10, 1))
    assert "rate limited" in res["1m"]["error"] and "rate limited" in res["5m"]["error"]
    with pytest.raises(ValueError):
        archive.collect("XAUUSD", ("15m",), root=tmp_path, downloader=boom)


def test_load_history_merges_the_archive(tmp_path, monkeypatch):
    from app.market import history
    monkeypatch.setattr(archive, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(history, "CANDLES_DIR", tmp_path / "candles")
    archive.collect("XAUUSD", ("5m",), downloader=fake_dl(freq="5min"), today=date(2026, 10, 1))
    df = history.load_history("XAUUSD", "5m")
    assert len(df) > 500 and df["timestamp"].is_monotonic_increasing
