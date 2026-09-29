"""Tests for app.broker: the mock broker and the live-trading safety gate."""

from __future__ import annotations

import pytest

from app.broker.base import (
    AccountSnapshot,
    BrokerAdapter,
    LiveTradingDisabledError,
    OrderRequest,
    OrderResult,
)
from app.broker.factory import BrokerNotAvailableError, get_broker
from app.broker.mock import MockBroker
from app.config import settings

NO_COST = settings.model_copy(update={"spread_pips": 0.0, "slippage_pips": 0.0, "commission_per_trade": 0.0})


def test_factory_returns_mock_even_with_live_trading_disabled():
    broker = get_broker("mock", settings.model_copy(update={"live_trading_enabled": False}))
    assert isinstance(broker, MockBroker)
    assert broker.is_live is False


def test_factory_fails_closed_for_any_real_broker_name():
    with pytest.raises(LiveTradingDisabledError):
        get_broker("oanda", settings.model_copy(update={"live_trading_enabled": False}))
    # Even with the switch on, nothing real exists to hand out.
    with pytest.raises(BrokerNotAvailableError):
        get_broker("oanda", settings.model_copy(update={"live_trading_enabled": True}))


class _FakeLiveBroker(BrokerAdapter):
    name = "fake-live"
    is_live = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self.submitted = []

    def _submit_order(self, order):
        self.submitted.append(order)
        return OrderResult(order.client_order_id, order.symbol, order.side, order.size, "FILLED", 1.0, self._now())

    def get_positions(self):
        return []

    def get_account(self):
        return AccountSnapshot(balance=0.0, equity=0.0, open_positions=0)


def test_live_adapter_cannot_submit_while_switch_is_off():
    """The gate is in the base class, so an adapter can't skip it."""
    broker = _FakeLiveBroker(settings.model_copy(update={"live_trading_enabled": False}))
    with pytest.raises(LiveTradingDisabledError):
        broker.place_order(OrderRequest("EURUSD=X", "BUY", 1000))
    assert broker.submitted == []


def test_order_request_validation():
    with pytest.raises(ValueError):
        OrderRequest("EURUSD=X", "HOLD", 1000)
    with pytest.raises(ValueError):
        OrderRequest("EURUSD=X", "BUY", 0)


def test_order_without_quote_is_rejected_not_filled():
    broker = MockBroker(NO_COST)
    result = broker.place_order(OrderRequest("EURUSD=X", "BUY", 1000))
    assert result.status == "REJECTED"
    assert broker.get_positions() == []


def test_round_trip_realizes_pnl():
    broker = MockBroker(NO_COST, starting_balance=10_000)
    broker.set_quote("EURUSD=X", 1.1000)
    broker.place_order(OrderRequest("EURUSD=X", "BUY", 10_000))
    broker.set_quote("EURUSD=X", 1.1050)

    acct = broker.get_account()
    assert acct.balance == pytest.approx(10_000)
    assert acct.equity == pytest.approx(10_050)

    result = broker.close_position("EURUSD=X")
    assert result is not None and result.side == "SELL"
    assert broker.get_positions() == []
    assert broker.get_account().balance == pytest.approx(10_050)
    assert broker.close_position("EURUSD=X") is None


def test_costs_are_charged_on_both_legs():
    cfg = settings.model_copy(update={"spread_pips": 1.0, "slippage_pips": 0.0, "commission_per_trade": 2.0, "pip_size": 0.0001})
    broker = MockBroker(cfg, starting_balance=10_000)
    broker.set_quote("EURUSD=X", 1.1000)
    buy = broker.place_order(OrderRequest("EURUSD=X", "BUY", 10_000))
    assert buy.fill_price == pytest.approx(1.1001)
    broker.close_position("EURUSD=X")
    # 1 pip each way on 10k units = $2, plus $2 commission per order.
    assert broker.get_account().balance == pytest.approx(10_000 - 2 - 4)


def test_partial_close_and_flip():
    broker = MockBroker(NO_COST, starting_balance=0)
    broker.set_quote("X", 100.0)
    broker.place_order(OrderRequest("X", "BUY", 10))
    broker.set_quote("X", 110.0)
    broker.place_order(OrderRequest("X", "SELL", 4))  # realize +40, 6 left at 100
    [pos] = broker.get_positions()
    assert pos.net_size == pytest.approx(6) and pos.average_price == pytest.approx(100.0)
    assert broker.balance == pytest.approx(40)

    broker.place_order(OrderRequest("X", "SELL", 10))  # close 6 (+60), open 4 short at 110
    [pos] = broker.get_positions()
    assert pos.net_size == pytest.approx(-4) and pos.average_price == pytest.approx(110.0)
    assert broker.balance == pytest.approx(100)
