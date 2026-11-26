"""Yahoo markets for the chart: catalogue, timeframes, closed candles, cache, assumed spread, and the API path."""

import pandas as pd
import pytest

from app.assistant.context import resample
from app.market import catalog
from app.market.catalog import CATALOG, YahooCharts, assumed_spread, category_of, digits_for, valid
from app.market.providers.mock_provider import generate_candles

NOW = pd.Timestamp("2026-11-20 14:07", tz="UTC")
M5 = generate_candles("XAUUSD", "5m", 12 * 24 * 400, "normal", seed=8)
M5 = M5.assign(timestamp=M5["timestamp"] - (M5["timestamp"].iloc[-1] - NOW.floor("5min")))   # ends with a forming candle


def fake_download(calls):
    def dl(symbol, interval, period):
        calls.append((symbol, interval))
        if symbol == "NOPE":
            raise RuntimeError("Yahoo returned no data")
        tf = {"5m": "5m", "15m": "15m", "60m": "1h", "1d": "1d"}[interval]
        return M5 if tf == "5m" else resample(M5, tf)
    return dl


def test_catalogue_covers_every_kind_and_symbols_are_checked():
    assert set(CATALOG) == {"Forex", "Commodities (futures)", "Index futures", "Stocks", "Crypto"}
    assert all(valid(s) for items in CATALOG.values() for s, _ in items)
    assert not valid("AAPL; rm") and not valid("") and not valid("../x")
    assert [category_of(s) for s in ("BTC-USD", "AUDJPY=X", "LE=F", "^GSPC", "SHOP")] == \
        ["Crypto", "Forex", "Commodities (futures)", "Index futures", "Stocks"]
    assert assumed_spread("EURUSD=X", 1.1) == pytest.approx(0.00011)
    assert [digits_for(p) for p in (60000, 50, 1.0972, 0.12)] == [2, 3, 5, 6]


def test_frames_closed_and_forming_with_4h_built_and_cached():
    calls = []
    t = [1000.0]
    y = YahooCharts(fake_download(calls), clock=lambda: t[0])
    closed, full = y.frames("BTC-USD", now=NOW)
    assert set(full) == {"5m", "15m", "1h", "4h", "1d"}
    assert full["5m"]["timestamp"].iloc[-1] == NOW.floor("5min") and closed["5m"]["timestamp"].iloc[-1] < NOW.floor("5min")
    assert (closed["1h"]["timestamp"] + pd.Timedelta("1h") <= NOW).all()
    n = len(calls)
    y.frames("BTC-USD", now=NOW)
    assert len(calls) == n                                         # within a minute: from the cache
    t[0] += 61
    y.frames("BTC-USD", now=NOW)
    assert len(calls) == n + 3                                     # intraday refreshed, daily kept 5 minutes
    with pytest.raises(LookupError):
        y.frames("NOPE", now=NOW)


def test_chart_api_yahoo_market(monkeypatch):
    import os
    from fastapi.testclient import TestClient
    os.environ["MARKET_DATA_PROVIDER"] = "MOCK"
    from app.api import chart as chart_api
    from app.main import app
    monkeypatch.setattr(chart_api, "YAHOO", YahooCharts(fake_download([])))
    with TestClient(app) as c:
        s = c.get("/api/chart/symbols").json()
        assert "Crypto" in s["catalog"] and s["catalog"]["Stocks"][0]["symbol"]
        d = c.get("/api/chart?symbol=BTC-USD&tf=4h").json()
        assert d["feed"] == "YAHOO" and d["category"] == "Crypto" and d["name"] == "Bitcoin" and d["spread_assumed"]
        assert d["candles"] and "robot_trades" not in d and d["analysis"].get("action") in ("BUY", "SELL", "WAIT")
        assert c.get("/api/chart?symbol=NOPE&tf=1h").status_code == 404
        assert c.get("/api/chart?symbol=bad%3Bsym&tf=1h").status_code == 422
        assert c.get("/api/chart?symbol=XAUUSD&tf=15m").json().get("feed") == "MOCK"   # the robot's own feed
    os.environ.pop("MARKET_DATA_PROVIDER", None)


class FakeResp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_yahoo_chart_endpoint_parsing():
    from app.market.catalog import yahoo_chart
    ts = [1795000000, 1795000300, 1795000600, 1795000600]
    payload = {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [
        {"open": [1, 2, None, 3], "high": [2, 3, None, 4], "low": [0.5, 1, None, 2], "close": [1.5, 2.5, None, 3.5],
         "volume": [10, None, 5, 7]}]}}]}}
    seen = {}

    class Client:
        def get(self, url, params, headers, timeout):
            seen.update(url=url, params=params, ua=headers.get("User-Agent"))
            return FakeResp(200, payload)
    df = yahoo_chart("BTC-USD", "5m", "55d", client=Client())
    assert seen["url"].endswith("/BTC-USD") and seen["params"]["interval"] == "5m" and seen["ua"]
    assert list(df["close"]) == [1.5, 2.5, 3.5] and df["timestamp"].is_unique     # gap dropped, duplicate kept last
    class Missing:
        def get(self, *a, **k):
            return FakeResp(404, {})
    with pytest.raises(LookupError):
        yahoo_chart("NOPE", "5m", "55d", client=Missing())
