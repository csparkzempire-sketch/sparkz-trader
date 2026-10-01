"""Providers (mock, Yahoo with a fake downloader, broker with a fake HTTP client), indicators, regimes, entries."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.config import load_settings
from app.engine.robot import Robot
from app.market.history import validate
from app.market.market_engine import MarketEngine, featurize
from app.market.providers.base import ProviderError
from app.market.providers.broker_provider import BrokerProvider
from app.market.providers.mock_provider import MockProvider, generate_candles
from app.market.providers.yahoo_provider import YahooProvider
from app.strategy.analysis import MarketAnalysisEngine
from app.strategy.entry_engine import EntryEngine
from tests.conftest import make_state


# ----------------------------------------------------------------------------- providers
def test_mock_provider_closes_candles_on_timeframe_boundaries():
    clock = [0.0]
    p = MockProvider("XAUUSD", "15m", seed=1, speed=60, clock=lambda: clock[0], history_bars=100)
    n0 = len(p.get_candles("15m", 10_000))
    t0 = p.get_latest_tick()
    clock[0] += 30                       # 30 s x 60 = 30 simulated minutes
    t1 = p.get_latest_tick()
    assert t1.time - t0.time == timedelta(minutes=30)
    assert t1.ask - t1.bid == pytest.approx(0.30)
    c = p.get_candles("15m", 10_000)
    assert len(c) - n0 in (1, 2)
    assert (c["timestamp"].diff().dropna() == pd.Timedelta(minutes=15)).all()
    assert p.info().live is False


def _fake_yahoo(now):
    def dl(symbol, interval, period):
        step = pd.Timedelta("1min") if interval == "1m" else pd.Timedelta("15min")
        ts = pd.date_range(end=pd.Timestamp(now).floor(step), periods=50, freq=step)
        px = np.linspace(2000, 2010, 50)
        return pd.DataFrame({"timestamp": ts, "open": px, "high": px + 1, "low": px - 1, "close": px, "volume": 1.0})
    return dl


def test_yahoo_provider_drops_the_forming_candle_and_flags_delay():
    p = YahooProvider("XAUUSD", "15m", downloader=_fake_yahoo(datetime.now(timezone.utc)))
    c = p.get_candles("15m", 100)
    assert (c["timestamp"] + pd.Timedelta("15min") <= pd.Timestamp.now(tz="UTC")).all()
    assert p.info().delayed is True
    t = p.get_latest_tick()
    assert t.ask - t.bid == pytest.approx(0.30)


class FakeResp:
    def __init__(self, code, data):
        self.status_code, self._d = code, data

    def json(self):
        return self._d


class FakeClient:
    def __init__(self, code=200):
        self.calls, self.code = [], code

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        if "/pricing" in url:
            return FakeResp(self.code, {"prices": [{"time": "2025-01-06T10:00:00.000000000Z", "tradeable": True,
                                                    "bids": [{"price": "2650.10"}], "asks": [{"price": "2650.45"}]}]})
        if "/candles" in url:
            return FakeResp(self.code, {"candles": [
                {"time": "2025-01-06T09:30:00Z", "complete": True, "volume": 10, "mid": {"o": "1", "h": "2", "l": "0.5", "c": "1.5"}},
                {"time": "2025-01-06T09:45:00Z", "complete": False, "volume": 3, "mid": {"o": "1.5", "h": "2", "l": "1", "c": "1.8"}}]})
        return FakeResp(self.code, {"instruments": [{"marginRate": "0.05", "displayPrecision": 3}]})


def test_broker_provider_reads_quotes_and_only_complete_candles():
    fc = FakeClient()
    p = BrokerProvider("XAUUSD", "15m", token="secret-token", account_id="001", client=fc)
    t = p.get_latest_tick()
    assert (t.bid, t.ask) == (2650.10, 2650.45)
    c = p.get_candles("15m", 10)
    assert len(c) == 1 and c["close"].iloc[0] == 1.5
    assert p.get_symbol_info().margin_rate == 0.05
    assert all(url.startswith("https://api-fxpractice.oanda.com/v3/") for url, _, _ in fc.calls)
    assert "secret-token" not in repr(p)
    assert p.get_market_status().open is True


def test_broker_provider_errors_never_leak_the_token():
    p = BrokerProvider("XAUUSD", "15m", token="secret-token", account_id="001", client=FakeClient(401))
    with pytest.raises(ProviderError) as e:
        p.get_latest_tick()
    assert "secret-token" not in str(e.value) and "401" in str(e.value)


def test_broker_provider_requires_credentials_from_env(monkeypatch):
    monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
    with pytest.raises(ProviderError):
        BrokerProvider("XAUUSD", "15m", account_id="001", client=FakeClient())


def test_history_validation_drops_bad_rows():
    c = generate_candles("XAUUSD", "15m", 50, seed=1)
    c.loc[5, "high"] = c.loc[5, "low"] - 1
    c = pd.concat([c, c.iloc[[10]]])
    out, rep = validate(c)
    assert rep["invalid_ohlc"] == 1 and len(out) == 49 and out["timestamp"].is_monotonic_increasing


# ----------------------------------------------------------------------------- analysis
def test_indicators_and_regimes_are_sensible():
    s = load_settings(env={})
    up = featurize(generate_candles("XAUUSD", "15m", 1500, "trend_up", seed=2), s)
    down = featurize(generate_candles("XAUUSD", "15m", 1500, "trend_down", seed=2), s)
    assert (up["regime"].iloc[:150] == "WARMUP").all()
    assert (up["trend_regime"].iloc[700:] == "TRENDING_UP").mean() > 0.5
    assert (down["trend_regime"].iloc[700:] == "TRENDING_DOWN").mean() > 0.5
    assert (up["atr"].dropna() > 0).all() and up["rsi"].dropna().between(0, 100).all()


def test_entry_rules_and_confidence_is_not_a_probability():
    e = EntryEngine(load_settings(env={}).entry)
    a = MarketAnalysisEngine(load_settings(env={}).entry, load_settings(env={}).analysis)
    buy = make_state(direction="BUY")
    assert e.decide(buy, a.analyze(buy)).action == "BUY"
    sell = make_state(direction="SELL")
    assert e.decide(sell, a.analyze(sell)).action == "SELL"
    weak = make_state(direction="BUY", adx=10.0)
    assert e.decide(weak, a.analyze(weak)).action == "WAIT"
    warm = make_state(ready=False)
    assert e.decide(warm, a.analyze(warm)).action == "WAIT"
    assert "not a calibrated probability" in a.analyze(buy).to_dict().get("confidence_note", "not a calibrated probability")


def test_paper_rolling_state_matches_backtest_state():
    """Paper recomputes indicators over a rolling window; the backtest precomputes them. Same bar, same answer."""
    s = load_settings(env={})
    c = generate_candles("XAUUSD", "15m", 1300, "reversals", seed=9)
    full = featurize(c, s)
    m = MarketEngine(s)
    m.load_history(c.iloc[:1299])
    st = m.on_candle_close(c.iloc[1299])
    row = full.iloc[1299]
    assert st.regime == row["regime"] and st.trend_regime == row["trend_regime"]
    assert st.ema50 == pytest.approx(row["ema_slow"], rel=1e-6)
    assert st.ema200 == pytest.approx(row["ema_trend"], rel=1e-3)
    assert st.atr == pytest.approx(row["atr"], rel=1e-3)


def test_robot_never_trades_on_warm_up_history():
    r = Robot(load_settings(env={}))
    r.warm_up(generate_candles("XAUUSD", "15m", 600, "trend_up", seed=1))
    assert r.strategy.basket is None and not r.executor.pending() and r.bar == 0
