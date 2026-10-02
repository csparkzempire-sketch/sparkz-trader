"""MT5 CSV import: MT5's tab-separated export, server-time conversion, spread stats, own store."""

import pandas as pd
import pytest

MT5_M1 = ("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
          "2025.01.06\t02:00:00\t2600.10\t2601.00\t2599.50\t2600.80\t120\t0\t25\n"
          "2025.01.06\t02:01:00\t2600.80\t2602.00\t2600.20\t2601.50\t98\t0\t30\n"
          "2025.01.06\t02:02:00\t2601.50\t2601.90\t2600.00\t2600.40\t110\t0\t45\n")


@pytest.fixture
def history(tmp_path, monkeypatch):
    from app.market import history
    monkeypatch.setattr(history, "CANDLES_DIR", tmp_path / "candles")
    return history


def test_mt5_export_goes_to_its_own_store_in_utc(tmp_path, history):
    f = tmp_path / "XAUUSD_M1.csv"
    f.write_text(MT5_M1)
    df, rep = history.import_csv(f, "XAUUSD", "1m", tz="Etc/GMT-2", source="mt5")
    assert rep["stored"] == 3 and rep["source"] == "mt5" and rep["unparsed_times"] == 0
    assert df["timestamp"].iloc[0] == pd.Timestamp("2025-01-06 00:00", tz="UTC")   # server GMT+2 -> UTC
    assert df["volume"].tolist() == [120, 98, 110]
    assert rep["spread_median"] == pytest.approx(0.30) and rep["spread_point"] == 0.01
    assert len(history.load_history("XAUUSD", "1m", "mt5")) == 3
    with pytest.raises(FileNotFoundError):
        history.load_history("XAUUSD", "1m", "oanda")


def test_mt5_spread_uses_point_size(tmp_path, history):
    f = tmp_path / "XAUUSD_M1.csv"
    f.write_text(MT5_M1)
    _, rep = history.import_csv(f, "XAUUSD", "1m", source="mt5", point=0.001)
    assert rep["spread_median"] == pytest.approx(0.030)


def test_missing_mt5_store_points_to_import(history):
    with pytest.raises(FileNotFoundError, match="import-csv"):
        history.load_history("XAUUSD", "15m", "mt5")
