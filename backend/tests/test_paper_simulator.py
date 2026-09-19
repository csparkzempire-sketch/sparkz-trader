from __future__ import annotations

import pytest

from app.config import Settings
from app.paper.simulator import PaperAccountState, PaperTradingSimulator
from app.risk.risk_manager import RiskManager


def _sim(**overrides) -> PaperTradingSimulator:
    cfg = Settings(**overrides)
    return PaperTradingSimulator(cfg=cfg, risk_manager=RiskManager(cfg))


def test_paper_account_has_no_broker_credential_fields():
    account = PaperAccountState(balance=10_000)
    field_names = set(vars(account).keys())
    for forbidden in ["api_key", "secret", "broker_token", "password"]:
        assert forbidden not in field_names


def test_open_and_close_position_updates_balance():
    sim = _sim(spread_pips=0.0, slippage_pips=0.0, pip_size=0.0001)
    account = PaperAccountState(balance=10_000)
    sim.start(account)

    pos = sim.open_position(account, symbol="EURUSD=X", direction="BUY", raw_price=1.1000, atr_value=0.0010)
    assert pos is not None
    assert "EURUSD=X" in account.open_positions

    trade = sim.close_position_at_market(account, symbol="EURUSD=X", raw_price=1.1050)
    assert trade is not None
    assert trade.pnl > 0  # price moved favorably for a BUY
    assert "EURUSD=X" not in account.open_positions
    assert account.balance == pytest.approx(10_000 + trade.pnl)


def test_stop_hit_closes_position_with_loss():
    sim = _sim(spread_pips=0.0, slippage_pips=0.0, stop_atr_multiplier=2.0, take_profit_r=2.0, pip_size=0.0001)
    account = PaperAccountState(balance=10_000)
    sim.start(account)
    pos = sim.open_position(account, symbol="EURUSD=X", direction="BUY", raw_price=1.1000, atr_value=0.0010)
    assert pos is not None

    trade = sim.check_and_close_if_hit(account, symbol="EURUSD=X", high=1.1005, low=pos.stop_price - 0.0001)
    assert trade is not None
    assert trade.reason == "STOP"
    assert trade.pnl < 0


def test_cannot_open_position_before_start():
    sim = _sim()
    account = PaperAccountState(balance=10_000)
    with pytest.raises(RuntimeError):
        sim.open_position(account, symbol="EURUSD=X", direction="BUY", raw_price=1.10, atr_value=0.001)


def test_no_real_broker_module_imported():
    """Guard against accidental live-broker integration sneaking into the
    paper simulator module."""
    import app.paper.simulator as mod
    source = open(mod.__file__).read().lower()
    for forbidden in ["import ccxt", "import alpaca", "import oanda", "requests.post(\"https://api.broker"]:
        assert forbidden not in source
