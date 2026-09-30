"""Tests for app.markets.instruments: per-market pip sizes, costs, and calendars."""

from __future__ import annotations

import pandas as pd
import pytest

from app.backtest.engine import BacktestConfig
from app.backtest.metrics import periods_per_year
from app.config import Settings, settings
from app.data.validator import validate_and_clean
from app.markets.instruments import resolve_costs
from app.paper.simulator import PaperTradingSimulator


def test_jpy_pairs_use_a_0_01_pip():
    """The bug this module fixes: a global 0.0001 pip made USD/JPY costs 100x too small."""
    cfg = BacktestConfig.from_settings(settings, "USDJPY=X", "1h")
    assert cfg.pip_size == 0.01
    assert BacktestConfig.from_settings(settings, "EURUSD=X", "1h").pip_size == 0.0001


def test_crypto_costs_are_material_in_price_terms():
    costs = resolve_costs("BTC-USD")
    assert costs.pip_size == 1.0
    assert (costs.spread_pips + costs.slippage_pips) * costs.pip_size >= 10  # dollars, not fractions of a cent


def test_unknown_symbols_fall_back_to_global_settings():
    costs = resolve_costs("AUDNZD=X", settings)
    assert costs.pip_size == settings.pip_size
    assert costs.spread_pips == settings.spread_pips


def test_explicit_per_run_override_beats_the_instrument_profile():
    cfg = settings.model_copy(update={"spread_pips": 0.0})
    costs = resolve_costs("USDJPY=X", cfg)
    assert costs.spread_pips == 0.0
    assert costs.pip_size == 0.01  # not overridden, so still the instrument's


def test_paper_simulator_charges_costs_in_each_symbols_own_pips():
    sim = PaperTradingSimulator(cfg=Settings())
    assert sim.costs_for("USDJPY=X").pip_size == 0.01
    assert sim.costs_for("GBPUSD=X").pip_size == 0.0001


def test_crypto_annualizes_on_calendar_days():
    assert periods_per_year("1h", "BTC-USD") == 365 * 24
    assert periods_per_year("1h", "EURUSD=X") == 252 * 24
    assert periods_per_year("1h") == 252 * 24


def _gappy(n=48):
    ts = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC").delete([10, 11])
    return pd.DataFrame({"timestamp": ts, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 1.0})


def test_validator_flags_crypto_gaps_as_real_data_holes():
    _, crypto = validate_and_clean(_gappy(), timeframe="1h", symbol="BTC-USD")
    _, fx = validate_and_clean(_gappy(), timeframe="1h", symbol="EURUSD=X")
    assert "24/7" in " ".join(crypto.notes)
    assert "weekends" in " ".join(fx.notes)


def test_instruments_endpoint_lists_profiles():
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/instruments").json()
    symbols = {i["symbol"]: i for i in body}
    assert {"EURUSD=X", "GBPUSD=X", "USDJPY=X", "GC=F", "BTC-USD", "ETH-USD"} <= set(symbols)
    assert symbols["GC=F"]["trades_24_7"] is False
    assert symbols["BTC-USD"]["trades_24_7"] is True


def test_gold_costs_are_in_dollars_not_fx_pips():
    """Without a profile, gold would get EUR/USD's 0.0001 pip and trade almost free."""
    costs = resolve_costs("GC=F")
    assert costs.pip_size == 0.1
    assert (costs.spread_pips + costs.slippage_pips) * costs.pip_size == pytest.approx(0.5)
    assert periods_per_year("1h", "GC=F") == 252 * 24  # not a 24/7 market
