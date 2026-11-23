"""MT5 DEMO execution: robot -> order book -> bridge API -> trader (fake MT5) -> result -> robot, and every refusal."""

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.execution.mt5_executor as mx
from app.api import bridge as bridge_api
from app.config import load_settings
from app.engine.robot import Robot
from app.execution.order_intent import IntentType, OrderIntent
from app.market.instruments import get_instrument
from app.market.providers.base import Tick
from app.market.providers.bridge_provider import BridgeStore
from tests.test_bridge import NOW, FakeMT5, mb

BRIDGE_DIR = Path(__file__).resolve().parents[2] / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))
spec = importlib.util.spec_from_file_location("mt5_trader", BRIDGE_DIR / "mt5_trader.py")
mt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mt)

TOKEN = "s3cret-bridge"
T = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class FakeTradingMT5(FakeMT5):
    """FakeMT5 plus the account, position and order calls the trader uses."""

    def __init__(self, demo=True, algo=True):
        super().__init__(minutes=300)
        self.demo, self.algo = demo, algo
        self.positions, self.sent, self.next_ticket = [], [], 1000
        self.bid, self.ask = 2650.10, 2650.40

    def account_info(self):
        return SimpleNamespace(trade_mode=0 if self.demo else 2, currency="USD", balance=1000.0, equity=1000.0,
                               margin_free=990.0, leverage=100, trade_allowed=True)

    def terminal_info(self):
        return SimpleNamespace(trade_allowed=self.algo)

    def symbol_info(self, symbol):
        return SimpleNamespace(point=0.001, digits=2, filling_mode=2)

    def symbol_info_tick(self, symbol):
        t = super().symbol_info_tick(symbol)
        t.bid, t.ask = self.bid, self.ask
        return t

    def positions_get(self, symbol=None):
        return tuple(self.positions)

    def order_send(self, req):
        self.sent.append(req)
        if not self.algo:
            return SimpleNamespace(retcode=10027, comment="AutoTrading disabled by client", price=0, volume=0, order=0)
        if "position" in req:
            self.positions = [p for p in self.positions if p.ticket != req["position"]]
        else:
            self.next_ticket += 1
            self.positions.append(SimpleNamespace(ticket=self.next_ticket, type=req["type"], volume=req["volume"],
                                                  price_open=req["price"], sl=req.get("sl", 0.0), profit=0.0,
                                                  magic=req["magic"]))
        return SimpleNamespace(retcode=10009, comment="done", price=req["price"], volume=req["volume"],
                               order=self.next_ticket)


@pytest.fixture
def book(monkeypatch):
    b = mx.OrderBook()
    monkeypatch.setattr(mx, "ORDERS", b)
    monkeypatch.setattr(bridge_api, "ORDERS", b)
    monkeypatch.setattr(bridge_api, "STORE", BridgeStore())
    monkeypatch.setenv("SPARKZ_BRIDGE_TOKEN", TOKEN)
    return b


@pytest.fixture
def client(book):
    app = FastAPI()
    app.include_router(bridge_api.router)
    return TestClient(app)


def http(client):
    def post(url, token, payload):
        r = client.post(url.replace("http://x", ""), json=payload, headers={"X-Bridge-Token": token})
        assert r.status_code == 200, r.text
        return r.json()

    def get(url, token):
        r = client.get(url.replace("http://x", ""), headers={"X-Bridge-Token": token})
        assert r.status_code == 200, r.text
        return r.json()
    return post, get


def demo_settings(mode="MT5_DEMO", **kw):
    return load_settings("assistant", env={}, overrides={"market": {"provider": "BRIDGE"},
                                                         "execution": {"mode": mode, **kw}})


def intent(kind=IntentType.OPEN_BASKET, lots=0.01, direction="BUY"):
    return OrderIntent(kind, "XAUUSD", direction, lots, "test", T, basket_id=None)


def tick():
    return Tick("XAUUSD", T, 2650.10, 2650.40)


def setup(client, demo=True, algo=True):
    post, get = http(client)
    mt5 = FakeTradingMT5(demo=demo, algo=algo)
    trader = mt.Trader(mt5, "XAUUSDm", "http://x", TOKEN, post, get, log=lambda m: None)
    bridge = mb.Bridge(mt5, "XAUUSDm", "XAUUSD", "http://x", TOKEN, utc_offset=3.0, post=post, log=lambda m: None,
                       clock=lambda: NOW.timestamp(), sleep=lambda s: None, history=300, trader=trader)
    bridge.connect()
    return mt5, trader, bridge


# ------------------------------------------------------------------ configuration
def test_mt5_demo_needs_the_bridge_and_backtests_stay_simulated():
    with pytest.raises(ValueError):
        load_settings(env={"EXECUTION_MODE": "MT5_DEMO"})
    with pytest.raises(ValueError):
        demo_settings(max_live_lots=0.5)                  # above the hard 0.1 ceiling
    s = demo_settings()
    assert Robot(s, mode="PAPER").live and not Robot(s, mode="BACKTEST").live
    assert not Robot(load_settings(env={}), mode="PAPER").live
    assert load_settings(env={}).live_trading_enabled is False


# ------------------------------------------------------------------ end to end
def test_open_and_close_round_trip_through_mt5(client, book):
    mt5, trader, bridge = setup(client)
    r = Robot(demo_settings(), mode="PAPER")
    r.last_tick = tick()
    bridge.step()                                           # account report: demo, algo trading on
    assert r.executor.account_problem() is None
    r.strategy.pending_open = True
    assert r._submit([intent()], T, 2650.25) == 1
    assert [o["kind"] for o in book.open_orders()] == ["OPEN"]
    bridge.step()                                           # bridge collects and executes in MT5
    req = mt5.sent[-1]
    assert req["volume"] == 0.01 and req["magic"] == mt.MAGIC and req["price"] == 2650.40
    assert req["sl"] == pytest.approx(2650.40 - 2 * 15 / 1.0)          # 2 x the 15 USD basket limit, 0.01 lot = 1 oz
    r.poll_executor()
    b = r.strategy.basket
    assert b is not None and b.positions[0].entry_price == pytest.approx(2650.40) and not r.strategy.pending_open
    assert "MT5 (DEMO account)" in " ".join(e["message"] for e in r.log.tail(20))

    assert r.close_basket_manually()
    bridge.step()
    assert mt5.positions == [] and mt5.sent[-1]["position"] > 0
    r.poll_executor()
    assert r.strategy.basket is None and len(r.completed) == 1


def test_basket_closed_in_mt5_is_booked_by_the_robot(client, book):
    mt5, trader, bridge = setup(client)
    r = Robot(demo_settings(), mode="PAPER")
    r.last_tick = tick()
    bridge.step()
    r.strategy.pending_open = True
    r._submit([intent()], T, 2650.25)
    bridge.step()
    r.poll_executor()
    assert r.strategy.basket is not None
    mt5.positions = []                                      # protective stop hit / closed by hand
    for _ in range(2):
        bridge.step()
        r.poll_executor()
    assert r.strategy.basket is None and r.completed[-1].close_reason == "BROKER_CLOSED"


def test_manual_positions_are_never_touched(client, book):
    mt5, trader, bridge = setup(client)
    mine = SimpleNamespace(ticket=1, type=0, volume=1.0, price_open=2600.0, sl=0.0, profit=0.0, magic=0)
    mt5.positions = [mine]
    r = Robot(demo_settings(), mode="PAPER")
    r.last_tick = tick()
    bridge.step()
    r.strategy.pending_open = True
    r._submit([intent()], T, 2650.25)
    bridge.step()
    r.poll_executor()
    r.close_basket_manually()
    bridge.step()
    assert mt5.positions == [mine]                          # only the SPARKZ position was closed


# ------------------------------------------------------------------ refusals
def test_real_account_is_refused_on_both_sides(client, book):
    post, get = http(client)
    real = FakeTradingMT5(demo=False)
    with pytest.raises(RuntimeError, match="REAL"):
        mt.Trader(real, "XAUUSDm", "http://x", TOKEN, post, get, log=lambda m: None).check_account()
    # a bridge that reports a real account: the server queues nothing
    book.set_account({"demo": False, "trade_allowed": True, "positions": []})
    r = Robot(demo_settings(), mode="PAPER")
    r.last_tick = tick()
    r.strategy.pending_open = True
    r._submit([intent()], T, 2650.25)
    assert book.open_orders() == []
    r.poll_executor()
    assert not r.strategy.pending_open
    assert "REAL account" in r.log.tail(5)[-1]["message"]
    # and the trader re-checks each order itself
    assert "DEMO" in mt.Trader(real, "XAUUSDm", "http://x", TOKEN, post, get, allow_real=False).refuse_reason(
        {"kind": "OPEN", "volume": 0.01, "age_seconds": 0})


def test_real_account_needs_mt5_live_on_the_server_and_live_on_the_bridge(client, book):
    acc = {"demo": False, "real_allowed": True, "trade_allowed": True, "equity": 500.0, "positions": []}
    book.set_account(acc)
    assert "MT5_DEMO" in mx.MT5BridgeExecutor(get_instrument("XAUUSD"), demo_settings(), book).account_problem()
    live = mx.MT5BridgeExecutor(get_instrument("XAUUSD"), demo_settings("MT5_LIVE"), book)
    assert live.account_problem() is None and live.account_type() == "REAL"
    book.set_account({**acc, "real_allowed": False})
    assert "--live" in live.account_problem()
    book.set_account({**acc, "demo": True, "real_allowed": False})        # MT5_LIVE also trades a demo account
    assert live.account_problem() is None and live.describe()["kind"] == "MT5_LIVE"


def test_real_account_round_trip_with_live_on_both_sides(client, book):
    post, get = http(client)
    mt5 = FakeTradingMT5(demo=False)
    trader = mt.Trader(mt5, "XAUUSDm", "http://x", TOKEN, post, get, allow_real=True, log=lambda m: None)
    trader.check_account()                                  # no error with --live
    bridge = mb.Bridge(mt5, "XAUUSDm", "XAUUSD", "http://x", TOKEN, utc_offset=3.0, post=post, log=lambda m: None,
                       clock=lambda: NOW.timestamp(), sleep=lambda s: None, history=300, trader=trader)
    bridge.connect()
    r = Robot(demo_settings("MT5_LIVE"), mode="PAPER")
    r.last_tick = tick()
    bridge.step()
    r.strategy.pending_open = True
    r._submit([intent()], T, 2650.25)
    bridge.step()
    r.poll_executor()
    assert r.strategy.basket is not None and len(mt5.positions) == 1
    assert "MT5 (REAL account)" in " ".join(e["message"] for e in r.log.tail(20))


def test_daily_loss_stop_blocks_new_trades_but_not_closes(client, book):
    acc = {"demo": True, "trade_allowed": True, "equity": 1000.0, "positions": []}
    book.set_account(acc)
    ex = mx.MT5BridgeExecutor(get_instrument("XAUUSD"), demo_settings(), book)
    assert ex.account_problem() is None                     # 1000 is today's start
    book.set_account({**acc, "equity": 975.0})
    assert ex.account_problem() is None                     # -2.5 % < 3 %
    book.set_account({**acc, "equity": 969.0})
    assert "daily loss stop" in ex.account_problem()
    ex.submit(intent(), T)
    assert book.open_orders() == [] and ex.take_rejections()
    ex.submit(intent(IntentType.CLOSE_BASKET), T)            # closing still goes through
    assert [o["kind"] for o in book.open_orders()] == ["CLOSE"]


def test_no_bridge_oversized_and_algo_off_orders_are_refused(client, book):
    r = Robot(demo_settings(), mode="PAPER")
    r.last_tick = tick()
    r.strategy.pending_open = True
    r._submit([intent()], T, 2650.25)                       # no account report yet
    r.poll_executor()
    assert "no recent account report" in r.log.tail(3)[-1]["message"]

    mt5, trader, bridge = setup(client, algo=False)
    bridge.step()
    assert "Algo Trading" in r.executor.account_problem()
    mt5.algo = True
    bridge.step()
    r._submit([intent(lots=0.02)], T, 2650.25)              # above max_live_lots 0.01
    r.poll_executor()
    assert "above the live limit" in r.log.tail(3)[-1]["message"] and mt5.sent == []
    assert "max-lots" in trader.refuse_reason({"kind": "OPEN", "volume": 0.05, "age_seconds": 0})


def test_mt5_refusal_and_stale_orders(client, book):
    mt5, trader, bridge = setup(client)
    clock = [0.0]
    book.clock = lambda: clock[0]
    r = Robot(demo_settings(), mode="PAPER")
    r.last_tick = tick()
    bridge.step()
    # an order the bridge never collects is dropped, not sent late
    r.strategy.pending_open = True
    r._submit([intent()], T, 2650.25)
    clock[0] += mx.ORDER_TTL + 1
    r.poll_executor()
    assert book.open_orders() == [] and "dropped" in r.log.tail(3)[-1]["message"]
    # MT5 itself refuses
    clock[0] = 0.0
    bridge.step()
    mt5.algo = False
    r.strategy.pending_open = True
    r._submit([intent()], T, 2650.25)
    bridge.step()
    r.poll_executor()
    assert "MT5 refused" in r.log.tail(3)[-1]["message"] and r.strategy.basket is None
    # the trader refuses an order that waited too long
    assert "not sent late" in trader.refuse_reason({"kind": "OPEN", "volume": 0.01, "age_seconds": 25})


def test_emergency_stop_cancels_orders_not_yet_collected(client, book):
    mt5, trader, bridge = setup(client)
    r = Robot(demo_settings(), mode="PAPER")
    r.last_tick = tick()
    bridge.step()
    r.strategy.pending_open = True
    r._submit([intent()], T, 2650.25)
    r.emergency_stop()
    assert book.open_orders() == []
    bridge.step()
    assert mt5.sent == []


def test_orders_endpoint_needs_the_token_and_hands_each_order_out_once(client, book):
    assert client.get("/api/bridge/orders").status_code == 401
    book.enabled = True
    book.add({"id": 7, "kind": "OPEN", "side": "BUY", "volume": 0.01})
    h = {"X-Bridge-Token": TOKEN}
    assert [o["id"] for o in client.get("/api/bridge/orders", headers=h).json()["orders"]] == [7]
    assert client.get("/api/bridge/orders", headers=h).json()["orders"] == []
    assert client.post("/api/bridge/orders/result", json={"id": 99, "status": "FILLED"}, headers=h).status_code == 404
    assert client.post("/api/bridge/orders/result", json={"id": 7, "status": "FILLED", "price": 2650.4},
                       headers=h).status_code == 200


def test_trader_never_executes_an_order_twice(client, book):
    mt5, trader, bridge = setup(client)
    o = {"id": 5, "kind": "OPEN", "side": "BUY", "volume": 0.01, "age_seconds": 0, "protective_distance": 30.0}
    trader.get = lambda url, token: {"orders": [o]}
    trader.post = lambda url, token, payload: None
    trader.step()
    trader.step()
    assert len(mt5.sent) == 1


def test_execution_report_measures_slippage_and_compares_with_history(client, book):
    from app.engine.live_report import build
    mt5, trader, bridge = setup(client)
    r = Robot(demo_settings(), mode="PAPER")
    r.last_tick = tick()
    r.executor.last_quote = Tick("XAUUSD", T, 2650.00, 2650.30)          # quote when the robot decided
    bridge.step()
    r.strategy.pending_open = True
    r._submit([intent()], T, 2650.15)
    bridge.step()                                                        # MT5 fills at its ask 2650.40
    r.poll_executor()
    rep = build(r)
    e = rep["execution"]
    assert e["fills"] == 1 and e["slippage"]["mean"] == pytest.approx(0.10)       # 0.10 worse than quoted
    assert e["spread_at_order"]["median"] == pytest.approx(0.30)
    assert rep["results"]["baskets"] == 0 and "at least 30" in rep["verdict"]
    r.close_basket_manually()
    bridge.step()
    r.poll_executor()
    rep = build(r)
    assert rep["results"]["baskets"] == 1 and rep["results"]["r_unit_usd"] == 15
    assert rep["execution"]["fills"] == 2
