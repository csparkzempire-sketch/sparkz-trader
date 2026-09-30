"""Phase 1: configuration, instruments, validation, storage, import and download."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest
from pydantic import ValidationError

from app.config import HARD_MAX_POSITIONS, SizingMode, load_config
from app.data.csv_import import read_csv
from app.data.downloader import download
from app.data.instruments import get_instrument
from app.data.repository import candle_inventory, load_candles, save_candles
from app.data.validator import DataValidationError, closed_candles, validate
from tests.conftest import make_candles


# --- configuration and parameter validation ---------------------------------------------------

def test_defaults_match_the_spec_and_are_safe():
    s = load_config(env={})
    assert (s.market.symbol, s.market.timeframe) == ("XAUUSD", "15m")
    assert s.sizing.mode == SizingMode.FIXED and s.sizing.allow_martingale is False
    assert s.live_trading_enabled is False and s.paper_trading is True
    assert s.risk.max_positions == 5


def test_flat_env_names_override_the_yaml():
    s = load_config(env={"SYMBOL": "EURUSD", "GRID_ATR_MULTIPLIER": "0.8", "MAX_POSITIONS": "3"})
    assert s.market.symbol == "EURUSD" and s.grid.atr_multiplier == 0.8 and s.risk.max_positions == 3


def test_live_trading_fails_closed():
    for key in ("LIVE_TRADING", "LIVE_TRADING_ENABLED"):
        with pytest.raises(ValidationError, match="Live trading is not implemented"):
            load_config(env={key: "true"})


def test_martingale_needs_explicit_permission():
    with pytest.raises(ValidationError, match="HIGH RISK"):
        load_config(env={"POSITION_SIZING": "MARTINGALE"})
    s = load_config(env={"POSITION_SIZING": "MARTINGALE", "ALLOW_MARTINGALE": "true"})
    assert s.sizing.mode == SizingMode.MARTINGALE


@pytest.mark.parametrize("env", [{"MAX_POSITIONS": str(HARD_MAX_POSITIONS + 1)}, {"MAX_POSITIONS": "0"},
                                 {"GRID_ATR_MULTIPLIER": "0"}, {"GRID_PRICE_STEP": "-1"},
                                 {"MAX_BASKET_LOSS_PERCENT": "0"}, {"BASE_LOT": "0"}])
def test_bad_parameters_are_rejected(env):
    with pytest.raises(ValidationError):
        load_config(env=env)


# --- instruments ---------------------------------------------------------------------------------

def test_gold_contract_math():
    g = get_instrument("XAUUSD")
    assert g.usd_per_price_unit(0.01, 2350) == pytest.approx(1.0)      # 0.01 lot = 1 oz: $1 per $1 move
    assert g.notional_usd(0.10, 2350) == pytest.approx(23_500)
    assert g.margin_usd(0.10, 2350) == pytest.approx(235)               # 1:100


def test_usdjpy_pnl_is_converted_to_usd():
    j = get_instrument("USDJPY")
    assert j.usd_per_price_unit(1.0, 150.0) == pytest.approx(100_000 / 150.0)
    assert j.notional_usd(1.0, 150.0) == pytest.approx(100_000)          # base currency is USD


def test_unknown_symbol():
    with pytest.raises(ValueError, match="Unknown symbol"):
        get_instrument("DOGE")


# --- validation ----------------------------------------------------------------------------------

def test_validator_cleans_and_reports():
    df = make_candles(10)
    bad = pd.concat([df, df.iloc[[3]]])                          # duplicate
    bad.loc[bad.index[5], "high"] = bad["low"].iloc[5] - 1      # high below low
    bad.loc[bad.index[6], "close"] = None                         # missing
    out, rep = validate(bad, "15m")
    assert rep.duplicates == 1 and rep.inconsistent_ohlc == 1 and rep.missing_values == 1
    assert out["timestamp"].is_monotonic_increasing and str(out["timestamp"].dt.tz) == "UTC"


def test_validator_converts_local_times_to_utc():
    df = make_candles(3)
    df["timestamp"] = pd.date_range("2026-03-02 09:00", periods=3, freq="15min", tz="America/New_York")
    out, _ = validate(df, "15m")
    assert out["timestamp"].iloc[0] == pd.Timestamp("2026-03-02 14:00", tz="UTC")


def test_validator_needs_columns_and_rows():
    with pytest.raises(DataValidationError, match="missing columns"):
        validate(pd.DataFrame({"timestamp": [], "open": []}), "15m")
    with pytest.raises(DataValidationError, match="valid rows"):
        validate(make_candles(3), "15m", min_rows=10)


def test_forming_candle_is_dropped():
    df = make_candles(4, t0="2026-01-05 00:00")
    now = datetime(2026, 1, 5, 0, 50, tzinfo=timezone.utc)   # 00:45 bar closes at 01:00 -> still forming
    assert closed_candles(df, "15m", now)["timestamp"].iloc[-1] == pd.Timestamp("2026-01-05 00:30", tz="UTC")


def test_gaps_ignore_weekends():
    df = make_candles(8, freq="1h", t0="2026-01-09 20:00")  # Friday evening
    df.loc[4:, "timestamp"] = df.loc[4:, "timestamp"] + pd.Timedelta(hours=46)  # resumes Sunday
    df.loc[6:, "timestamp"] = df.loc[6:, "timestamp"] + pd.Timedelta(hours=5)   # real gap
    assert validate(df, "1h")[1].gaps == 1


# --- storage ------------------------------------------------------------------------------------

def test_repository_round_trip_and_upsert():
    df = make_candles(50)
    assert save_candles(df, "XAUUSD", "15m", "test") == 50
    changed = df.iloc[:10].copy()
    changed["close"] = changed[["open", "high"]].min(axis=1)
    save_candles(changed, "XAUUSD", "15m", "test")                 # overwrite, no duplicates
    back = load_candles("XAUUSD", "15m")
    assert len(back) == 50 and back["close"].iloc[0] == pytest.approx(changed["close"].iloc[0])
    assert candle_inventory()[0]["bars"] == 50
    assert len(load_candles("XAUUSD", "15m", start="2026-01-05 01:00")) == 46


# --- CSV import and download -----------------------------------------------------------------------

def test_mt5_csv_import_converts_server_time_and_spread_points(tmp_path):
    p = tmp_path / "XAUUSD_M15.csv"
    p.write_text("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
                 "2026.01.05\t02:00:00\t2350.10\t2351.00\t2349.50\t2350.80\t120\t0\t25\n"
                 "2026.01.05\t02:15:00\t2350.80\t2352.00\t2350.20\t2351.50\t130\t0\t30\n")
    df, rep = read_csv(p, "XAUUSD", "15m", tz="Etc/GMT-2")   # server time UTC+2
    assert rep["mt5_format"] and df["timestamp"].iloc[0] == pd.Timestamp("2026-01-05 00:00", tz="UTC")
    assert df["spread"].tolist() == pytest.approx([0.25, 0.30])


def test_generic_csv_import(tmp_path):
    p = tmp_path / "eur.csv"
    make_candles(5).to_csv(p, index=False)
    df, rep = read_csv(p, "EURUSD", "15m")
    assert len(df) == 5 and not rep["mt5_format"]


def test_download_resamples_4h_and_drops_the_forming_bar():
    hourly = make_candles(48, freq="1h", t0="2026-01-05 00:00")

    def fake_fetch(ticker, interval, period):
        assert (ticker, interval) == ("GC=F", "1h")
        return hourly

    df, info = download("XAUUSD", "4h", fetch=fake_fetch)
    assert len(df) == 12 and info["source"] == "yahoo:GC=F" and "proxy" in info["note"]
    first = hourly.iloc[:4]
    assert df["high"].iloc[0] == pytest.approx(first["high"].max()) and df["close"].iloc[0] == pytest.approx(first["close"].iloc[-1])
