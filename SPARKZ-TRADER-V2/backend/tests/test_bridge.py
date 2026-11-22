"""MT5 bridge: script (fake MT5) -> push endpoint (token, validation) -> BridgeProvider -> paper runner."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import bridge as bridge_api
from app.config import load_settings
from app.market.providers.bridge_provider import BridgeProvider, BridgeStore
from app.paper.runner import PaperRunner

SCRIPT = Path(__file__).resolve().parents[2] / "bridge" / "mt5_bridge.py"
spec = importlib.util.spec_from_file_location("mt5_bridge", SCRIPT)
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)

OFFSET = 3.0                                             # broker server time = UTC+3
NOW = pd.Timestamp("2026-10-01 12:00:30", tz="UTC")       # a Thursday, market open


class FakeMT5:
    """The read calls of the MetaTrader5 package, in server time (UTC+3), 1m candles up to NOW."""
    TIMEFRAME_M1 = 1

    def __init__(self, minutes=3000):
        self.end = NOW.floor("1min")
        self.minutes = minutes
        self.calls = []

    def _server_epoch(self, ts):
        return (ts + pd.Timedelta(hours=OFFSET)).timestamp()

    def initialize(self):
        return True

    def last_error(self):
        return (0, "ok")

    def symbol_select(self, symbol, on):
        return symbol == "XAUUSDm"

    def symbol_info(self, symbol):
        return SimpleNamespace(point=0.001)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(time_msc=int(self._server_epoch(NOW - pd.Timedelta("1s")) * 1000), bid=2650.10, ask=2650.40)

    def copy_rates_from_pos(self, symbol, tf, pos, count):
        self.calls.append((pos, count))
        times = pd.date_range(end=self.end - pd.Timedelta(minutes=pos), periods=min(count, self.minutes), freq="1min")
        c = 2650 + np.sin(np.arange(len(times)) / 30)
        rec = np.zeros(len(times), dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                                          ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4")])
        rec["time"] = [int(self._server_epoch(t)) for t in times]
        rec["open"], rec["close"] = c, c
        rec["high"], rec["low"], rec["tick_volume"] = c + 0.3, c - 0.3, 10
        rec["spread"] = np.where(np.arange(len(times)) % 2, 200, 300)      # points: 0.200 / 0.300
        return rec


@pytest.fixture
def server(monkeypatch):
    store = BridgeStore(clock=lambda: NOW.timestamp())
    monkeypatch.setattr(bridge_api, "STORE", store)
    monkeypatch.setenv("SPARKZ_BRIDGE_TOKEN", "s3cret-bridge")
    monkeypatch.setattr(bridge_api, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW.to_pydatetime())}))
    app = FastAPI()
    app.include_router(bridge_api.router)
    return TestClient(app), store


def via_client(client):
    def post(url, token, payload):
        r = client.post("/api/bridge/push", json=payload, headers={"X-Bridge-Token": token})
        assert r.status_code == 200, r.text
        return r.json()
    return post


def test_offset_detection():
    utc = NOW.timestamp()
    assert mb.detect_offset_hours(utc + 3 * 3600 + 4, utc) == 3.0
    assert mb.detect_offset_hours(utc - 5.5 * 3600 - 30, utc) == -5.5
    assert mb.detect_offset_hours(utc + 3 * 3600 + 20 * 60, utc) is None      # stale tick: market closed


def test_bridge_end_to_end_into_the_provider(server):
    client, store = server
    mt5 = FakeMT5()
    b = mb.Bridge(mt5, "XAUUSDm", "XAUUSD", "http://x", "s3cret-bridge", post=via_client(client),
                  clock=lambda: NOW.timestamp(), sleep=lambda s: None, log=lambda m: None, history=3000)
    b.run(max_steps=2)
    assert b.offset == 3.0
    assert mt5.calls[0] == (1, 3000)                    # history starts at position 1: closed candles only
    assert store.status()["bars_1m"] == 3000 and store.pushes >= 3     # 2 history chunks + steps
    p = BridgeProvider("XAUUSD", "15m", store=store)
    tick = p.get_latest_tick()
    assert tick.time == (NOW - pd.Timedelta("1s")).to_pydatetime() and tick.spread == pytest.approx(0.30)
    m1 = store.frame()
    assert m1["timestamp"].iloc[-1] == NOW.floor("1min") - pd.Timedelta(minutes=1)   # server time -> UTC
    c15 = p.get_candles("15m", 10)
    assert (c15["timestamp"] + pd.Timedelta(minutes=15) <= m1["timestamp"].iloc[-1] + pd.Timedelta(minutes=1)).all()
    assert c15["timestamp"].iloc[-1] == pd.Timestamp("2026-10-01 11:45", tz="UTC")   # 11:45-12:00 just closed
    assert p.info().name == "MT5 bridge (XAUUSDm)" and p.get_market_status().open


def test_push_needs_the_token(server, monkeypatch):
    client, _ = server
    body = {"symbol": "XAUUSD", "broker_symbol": "XAUUSD"}
    assert client.post("/api/bridge/push", json=body).status_code == 401
    assert client.post("/api/bridge/push", json=body, headers={"X-Bridge-Token": "nope"}).status_code == 401
    monkeypatch.delenv("SPARKZ_BRIDGE_TOKEN")
    r = client.post("/api/bridge/push", json=body, headers={"X-Bridge-Token": "s3cret-bridge"})
    assert r.status_code == 503 and "s3cret" not in r.text
    assert client.get("/api/bridge/status").json()["enabled"] is False


@pytest.mark.parametrize("bad", [
    {"tick": {"time": "2026-10-01T12:00:00Z", "bid": 2650.5, "ask": 2650.1}},                  # ask below bid
    {"tick": {"time": "2026-10-01T12:00:00", "bid": 2650.1, "ask": 2650.5}},                   # no UTC offset
    {"bars_1m": [{"time": "2026-10-01T11:00:00Z", "open": 1, "high": 0.5, "low": 0.4, "close": 1}]},  # bad OHLC
])
def test_push_rejects_bad_data(server, bad):
    client, store = server
    r = client.post("/api/bridge/push", json={"symbol": "XAUUSD", "broker_symbol": "X", **bad},
                    headers={"X-Bridge-Token": "s3cret-bridge"})
    assert r.status_code == 422 and store.pushes == 0


def test_push_rejects_future_times_from_a_wrong_offset(server):
    client, _ = server
    t = (NOW + pd.Timedelta(hours=3)).isoformat()        # server time sent as UTC
    r = client.post("/api/bridge/push", json={"symbol": "XAUUSD", "broker_symbol": "X",
                                              "tick": {"time": t, "bid": 2650.1, "ask": 2650.4}},
                    headers={"X-Bridge-Token": "s3cret-bridge"})
    assert r.status_code == 422 and "utc-offset" in r.json()["detail"]


def test_market_closed_when_ticks_stop_and_symbol_mismatch():
    clock = [NOW.timestamp()]
    store = BridgeStore(clock=lambda: clock[0])
    p = BridgeProvider("XAUUSD", "15m", store=store)
    assert not p.get_market_status().open
    from app.market.providers.base import ProviderError, Tick
    store.push("XAUUSD", "XAUUSD", Tick("XAUUSD", NOW.to_pydatetime(), 1.0, 1.1), [])
    assert p.get_market_status().open
    clock[0] += 300
    assert "no tick" in p.get_market_status().reason
    store.push("EURUSD", "EURUSD", None, [])
    with pytest.raises(ProviderError):
        p.get_latest_tick()


def test_paper_runner_warms_up_once_the_bridge_connects(tmp_path):
    store = BridgeStore(clock=lambda: NOW.timestamp())
    r = PaperRunner(load_settings(env={}, overrides={"market": {"history_bars": 100}}),
                    BridgeProvider("XAUUSD", "15m", store=store), None, wall=lambda: NOW.timestamp())
    r.start()                                            # nothing pushed yet
    assert r.last_bar_ts is None
    mt5 = FakeMT5()
    rates = mt5.copy_rates_from_pos("XAUUSDm", 1, 1, 3000)
    store.push("XAUUSD", "XAUUSDm", None, mb.bars_payload(rates, OFFSET))
    r.step()
    assert r.last_bar_ts == pd.Timestamp("2026-10-01 11:45", tz="UTC")
    assert len(r.robot.market.candles) >= 100


def test_bridge_script_never_calls_an_order_function():
    tree = ast.parse(SCRIPT.read_text())
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
            {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    forbidden = {"order_send", "order_check", "order_calc_margin", "order_calc_profit", "positions_get",
                 "orders_get", "Close", "Buy", "Sell"}
    assert not names & forbidden


def test_spread_report_from_candles_and_live_ticks(server):
    client, store = server
    mt5 = FakeMT5(minutes=200)
    b = mb.Bridge(mt5, "XAUUSDm", "XAUUSD", "http://x", "s3cret-bridge", post=via_client(client),
                  clock=lambda: NOW.timestamp(), sleep=lambda s: None, log=lambda m: None, history=200)
    b.run(max_steps=3)                                   # the fake repeats the same quote: counted once
    rep = client.get("/api/bridge/spread").json()
    assert rep["mt5_1m_candles"]["count"] == 200
    assert rep["mt5_1m_candles"]["median"] == pytest.approx(0.25)          # half 0.200, half 0.300
    assert rep["mt5_1m_candles"]["max"] == pytest.approx(0.30)
    assert rep["live_ticks"]["count"] == 1 and rep["live_ticks"]["median"] == pytest.approx(0.30)
    assert rep["live_ticks"]["by_session_utc"]["NEW_YORK"]["count"] == 1         # 12:00 UTC
    assert rep["backtest_assumption"] == pytest.approx(0.54) and "0.6x" in rep["vs_backtest"]


def test_bars_without_a_point_size_send_no_spread():
    rates = FakeMT5(minutes=3).copy_rates_from_pos("XAUUSDm", 1, 1, 3)
    assert "spread" not in mb.bars_payload(rates, OFFSET)[0]
    assert mb.bars_payload(rates, OFFSET, 0.01)[0]["spread"] == pytest.approx(3.0)


class FakeMT5WithHTF(FakeMT5):
    TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 16385, 16388, 16408
    FREQ = {1: "1min", 16385: "1h", 16388: "4h", 16408: "1D"}

    def copy_rates_from_pos(self, symbol, tf, pos, count):
        if tf == 1:
            return super().copy_rates_from_pos(symbol, tf, pos, count)
        freq = self.FREQ[tf]
        end = (NOW + pd.Timedelta(hours=OFFSET)).floor(freq) - pd.Timedelta(freq) * pos      # server-time candles
        times = pd.date_range(end=end, periods=count, freq=freq)
        rec = np.zeros(len(times), dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                                          ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4")])
        rec["time"] = [int(t.tz_convert(None).value // 10**9) if t.tzinfo else int(t.value // 10**9) for t in times]
        c = 2600 + np.arange(len(times)) * 0.1
        rec["open"], rec["close"], rec["high"], rec["low"], rec["tick_volume"] = c, c, c + 2, c - 2, 100
        return rec


def test_bridge_sends_higher_timeframes_for_the_assistant(server):
    client, store = server
    b = mb.Bridge(FakeMT5WithHTF(minutes=600), "XAUUSDm", "XAUUSD", "http://x", "s3cret-bridge",
                  post=via_client(client), clock=lambda: NOW.timestamp(), sleep=lambda s: None,
                  log=lambda m: None, history=600)
    b.run(max_steps=1)
    st = store.status()["higher_timeframes"]
    assert st == {"1h": 2000, "4h": 1000, "1d": 600}
    p = BridgeProvider("XAUUSD", "15m", store=store)
    d1 = p.get_candles("1d", 1000)
    # last CLOSED server-time day at 15:00 server time on 1 Oct is 30 Sep 00:00 server = 29 Sep 21:00 UTC
    assert len(d1) == 600 and d1["timestamp"].iloc[-1] == pd.Timestamp("2026-09-29 21:00", tz="UTC")
    h4 = p.get_candles("4h", 5000)
    assert h4["timestamp"].is_monotonic_increasing and h4["timestamp"].is_unique
    assert len(h4) >= 1000                                       # native candles, then newer ones from 1m
