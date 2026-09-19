from __future__ import annotations

import pytest

from app.risk.position_sizing import calculate_position_size
from app.risk.stops import calculate_stop_and_target
from app.risk.risk_manager import RiskManager, RiskState
from app.config import Settings


def test_position_sizing_basic():
    result = calculate_position_size(account_equity=10_000, risk_per_trade=0.01, entry_price=1.1000, stop_price=1.0950)
    assert result.stop_distance == pytest.approx(0.005)
    assert result.dollar_risk == pytest.approx(100.0)
    assert result.position_size == pytest.approx(100.0 / 0.005)


def test_position_sizing_rejects_zero_stop_distance():
    with pytest.raises(ValueError):
        calculate_position_size(account_equity=10_000, risk_per_trade=0.01, entry_price=1.10, stop_price=1.10)


def test_position_sizing_rejects_invalid_risk_fraction():
    with pytest.raises(ValueError):
        calculate_position_size(account_equity=10_000, risk_per_trade=1.5, entry_price=1.10, stop_price=1.09)


def test_stop_and_target_buy_direction():
    result = calculate_stop_and_target(entry_price=1.1000, atr_value=0.0010, direction="BUY", stop_atr_multiplier=2.0, take_profit_r=2.0)
    assert result.stop_price == pytest.approx(1.1000 - 0.0020)
    assert result.target_price == pytest.approx(1.1000 + 0.0040)


def test_stop_and_target_sell_direction():
    result = calculate_stop_and_target(entry_price=1.1000, atr_value=0.0010, direction="SELL", stop_atr_multiplier=2.0, take_profit_r=2.0)
    assert result.stop_price == pytest.approx(1.1000 + 0.0020)
    assert result.target_price == pytest.approx(1.1000 - 0.0040)


def test_risk_manager_blocks_when_max_positions_reached():
    cfg = Settings(max_simultaneous_positions=1)
    rm = RiskManager(cfg)
    state = RiskState(equity=10_000, peak_equity=10_000, open_positions=1)
    check = rm.check_new_trade(state, proposed_dollar_risk=100)
    assert not check.allowed


def test_risk_manager_blocks_on_max_drawdown():
    cfg = Settings(max_drawdown_pct=0.10)
    rm = RiskManager(cfg)
    state = RiskState(equity=8_900, peak_equity=10_000)  # 11% drawdown
    check = rm.check_new_trade(state, proposed_dollar_risk=100)
    assert not check.allowed
    assert "drawdown" in check.reason


def test_risk_manager_allows_when_within_limits():
    cfg = Settings()
    rm = RiskManager(cfg)
    state = RiskState(equity=10_000, peak_equity=10_000)
    check = rm.check_new_trade(state, proposed_dollar_risk=50)
    assert check.allowed
