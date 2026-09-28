"""Tests for app.data.repository.merge_into_cache.

Motivation: `download-data` used to overwrite the cached parquet file. With
Yahoo now only returning short windows of hourly data, one successful short
download would have silently replaced ~2 years of history with a few weeks.
"""

from __future__ import annotations

import pandas as pd
import pytest

import app.data.repository as repo


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(repo, "DATA_DIR", tmp_path / "data")


def _bars(start: str, n: int, close: float = 1.10, unit: str | None = None) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    if unit:
        ts = ts.astype(f"datetime64[{unit}, UTC]")
    return pd.DataFrame(
        {"timestamp": ts, "open": close, "high": close + 0.01, "low": close - 0.01, "close": close, "volume": 0.0}
    )


def test_first_merge_creates_cache():
    merged, path, n_before = repo.merge_into_cache(_bars("2024-01-01", 100), "EURUSD=X", "1h")
    assert n_before == 0
    assert len(merged) == 100
    assert path.exists()
    assert repo.cache_exists("EURUSD=X", "1h")


def test_short_download_never_shrinks_existing_history():
    """The whole point: a 50-bar download must not replace a 1000-bar cache."""
    repo.merge_into_cache(_bars("2024-01-01", 1000), "EURUSD=X", "1h")
    merged, _, n_before = repo.merge_into_cache(_bars("2024-02-01", 50), "EURUSD=X", "1h")
    assert n_before == 1000
    assert len(merged) >= 1000
    assert merged["timestamp"].iloc[0] == pd.Timestamp("2024-01-01", tz="UTC")


def test_new_data_extends_history_and_stays_sorted_and_unique():
    repo.merge_into_cache(_bars("2024-01-01", 100), "EURUSD=X", "1h")  # Jan 1 .. Jan 5 04:00
    merged, _, _ = repo.merge_into_cache(_bars("2024-01-04", 100), "EURUSD=X", "1h")  # overlaps, then extends
    assert merged["timestamp"].is_monotonic_increasing
    assert not merged["timestamp"].duplicated().any()
    assert merged["timestamp"].iloc[-1] == pd.Timestamp("2024-01-04", tz="UTC") + pd.Timedelta(hours=99)


def test_on_duplicate_timestamp_the_newer_download_wins():
    repo.merge_into_cache(_bars("2024-01-01", 10, close=1.10), "EURUSD=X", "1h")
    merged, _, _ = repo.merge_into_cache(_bars("2024-01-01", 10, close=1.25), "EURUSD=X", "1h")
    assert len(merged) == 10
    assert (merged["close"] == 1.25).all()


def test_mixed_timestamp_resolutions_merge_cleanly():
    """Cached parquet and a fresh download can differ in datetime64 unit
    ([s] vs [ns]); that must not raise or produce duplicate bars."""
    repo.merge_into_cache(_bars("2024-01-01", 50, unit="s"), "EURUSD=X", "1h")
    merged, _, _ = repo.merge_into_cache(_bars("2024-01-01", 80, unit="ns"), "EURUSD=X", "1h")
    assert len(merged) == 80
    assert not merged["timestamp"].duplicated().any()


def test_load_processed_roundtrips_after_merge():
    repo.merge_into_cache(_bars("2024-01-01", 30), "BTC-USD", "1h")
    loaded = repo.load_processed("BTC-USD", "1h")
    assert len(loaded) == 30
    assert str(loaded["timestamp"].dt.tz) == "UTC"
