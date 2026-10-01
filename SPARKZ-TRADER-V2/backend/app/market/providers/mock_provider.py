"""
Synthetic market data, for development and stress tests.

Two uses:
- `generate_candles(...)`: a whole OHLC series for a named scenario. The stress
  tests use these to put the strategy into situations real history may not
  contain often enough (a relentless trend against the basket, news spikes...).
- `MockProvider`: a "live" feed. Prices evolve in simulated time (wall clock x
  `speed`), ticks carry a bid/ask spread, and candles close on timeframe
  boundaries, exactly as a broker feed would deliver them.

Scenarios (per-bar log-return processes, volatility in fractions per bar):
  normal        random walk, no drift
  trend_up      steady positive drift with noise
  trend_down    steady negative drift with noise
  sideways      mean-reverting around the start price (Ornstein-Uhlenbeck)
  high_vol      random walk with 3x volatility
  spike         normal, then one sudden jump of `spike_atr` ATRs at the middle
  reversals     alternating up/down trends every `leg_bars` bars
  news_spikes   normal plus random large shocks (about 1 in 150 bars)
  adverse_trend a relentless move UP (the critical failure test for SELL baskets)

Synthetic data is never presented as market history: reports label it.
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.config import TIMEFRAMES
from app.market.instruments import Instrument, get_instrument
from app.market.providers.base import MarketDataProvider, MarketStatus, ProviderInfo, Tick

SCENARIOS = ["normal", "trend_up", "trend_down", "sideways", "high_vol", "spike", "reversals", "news_spikes",
             "adverse_trend"]
START_PRICES = {"XAUUSD": 2350.0, "EURUSD": 1.08, "GBPUSD": 1.27, "USDJPY": 150.0, "BTCUSD": 60_000.0}
DAILY_VOL = {"XAUUSD": 0.011, "EURUSD": 0.005, "GBPUSD": 0.006, "USDJPY": 0.006, "BTCUSD": 0.03}


def _bar_vol(symbol: str, timeframe: str) -> float:
    return DAILY_VOL.get(symbol, 0.01) * np.sqrt(TIMEFRAMES[timeframe] / 86_400)


def scenario_returns(scenario: str, n: int, vol: float, rng: np.random.Generator,
                     spike_atr: float = 8.0, leg_bars: int = 120) -> np.ndarray:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {', '.join(SCENARIOS)}")
    eps = rng.standard_normal(n)
    if scenario == "normal":
        return vol * eps
    if scenario == "trend_up":
        return vol * eps + 0.25 * vol
    if scenario == "trend_down":
        return vol * eps - 0.25 * vol
    if scenario == "adverse_trend":
        return vol * 0.6 * eps + 0.6 * vol
    if scenario == "high_vol":
        return 3 * vol * eps
    if scenario == "sideways":
        out, level = np.empty(n), 0.0
        for k in range(n):
            step = -0.05 * level + vol * eps[k]
            level += step
            out[k] = step
        return out
    if scenario == "spike":
        r = vol * eps
        r[n // 2] += spike_atr * vol * (1 if rng.random() < 0.5 else -1)
        return r
    if scenario == "reversals":
        sign = np.where((np.arange(n) // leg_bars) % 2 == 0, 1.0, -1.0)
        return vol * eps + 0.35 * vol * sign
    # news_spikes
    shocks = (rng.random(n) < 1 / 150) * rng.choice([-1, 1], n) * rng.uniform(5, 12, n) * vol
    return vol * eps + shocks


def generate_candles(symbol: str = "XAUUSD", timeframe: str = "15m", n: int = 2000, scenario: str = "normal",
                     seed: int = 0, start_price: float | None = None,
                     start: datetime | None = None, **kw) -> pd.DataFrame:
    """OHLC candles for a scenario. Intrabar highs and lows extend beyond open/close by a random
    share of the bar's volatility, so grids and targets can trigger inside a bar."""
    rng = np.random.default_rng(seed)
    vol = _bar_vol(symbol, timeframe)
    r = scenario_returns(scenario, n, vol, rng, **kw)
    p0 = start_price or START_PRICES.get(symbol, 100.0)
    close = p0 * np.exp(np.cumsum(r))
    open_ = np.concatenate([[p0], close[:-1]])
    wick = np.abs(rng.normal(0, 0.5 * vol, (n, 2))) * close[:, None]
    high = np.maximum(open_, close) + wick[:, 0]
    low = np.minimum(open_, close) - wick[:, 1]
    step = TIMEFRAMES[timeframe]
    t0 = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    ts = pd.date_range(t0, periods=n, freq=f"{step}s", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.integers(100, 1000, n).astype(float)})


class MockProvider(MarketDataProvider):
    """A synthetic live feed. `speed` = simulated seconds per wall-clock second."""

    def __init__(self, symbol: str = "XAUUSD", timeframe: str = "15m", scenario: str = "normal", seed: int = 0,
                 speed: float = 60.0, history_bars: int = 600, clock=None):
        self.instrument: Instrument = get_instrument(symbol)
        self.timeframe, self.scenario, self.speed = timeframe, scenario, speed
        self.step = TIMEFRAMES[timeframe]
        self.rng = np.random.default_rng(seed)
        self._clock = clock or _time.time
        now = datetime.now(timezone.utc)
        self._bar_start = datetime.fromtimestamp((int(now.timestamp()) // self.step) * self.step, tz=timezone.utc)
        hist = generate_candles(symbol, timeframe, history_bars, scenario, seed,
                                start=self._bar_start - timedelta(seconds=self.step * history_bars))
        self._closed = hist
        p = float(hist["close"].iloc[-1])
        self._price = p
        self._cur = {"timestamp": self._bar_start, "open": p, "high": p, "low": p, "close": p, "volume": 0.0}
        self._sim_now = self._bar_start
        self._wall = self._clock()
        self._sec_vol = DAILY_VOL.get(symbol, 0.01) / np.sqrt(86_400)
        self._drift = {"trend_up": 0.25, "trend_down": -0.25, "adverse_trend": 0.6}.get(scenario, 0.0) \
            * _bar_vol(symbol, timeframe) / self.step

    def info(self) -> ProviderInfo:
        return ProviderInfo("Mock (synthetic)", "MOCK", live=False,
                            notes=[f"synthetic '{self.scenario}' prices at {self.speed:g}x speed; not market data"])

    def _advance(self) -> None:
        wall = self._clock()
        sim_seconds = int((wall - self._wall) * self.speed)
        if sim_seconds <= 0:
            return
        self._wall = wall
        vol = self._sec_vol * (3 if self.scenario == "high_vol" else 1)
        steps = self.rng.standard_normal(sim_seconds) * vol + self._drift
        for k in range(sim_seconds):
            self._sim_now += timedelta(seconds=1)
            if self._sim_now >= self._cur["timestamp"] + timedelta(seconds=self.step):
                self._closed = pd.concat([self._closed, pd.DataFrame([self._cur])], ignore_index=True).tail(5000)
                self._cur = {"timestamp": self._cur["timestamp"] + timedelta(seconds=self.step), "open": self._price,
                             "high": self._price, "low": self._price, "close": self._price, "volume": 0.0}
            self._price *= float(np.exp(steps[k]))
            c = self._cur
            c["high"], c["low"], c["close"] = max(c["high"], self._price), min(c["low"], self._price), self._price
            c["volume"] += 1

    def get_latest_tick(self) -> Tick:
        self._advance()
        half = self.instrument.spread / 2
        return Tick(self.instrument.symbol, self._sim_now, self._price - half, self._price + half)

    def get_candles(self, timeframe: str, count: int) -> pd.DataFrame:
        if timeframe != self.timeframe:
            raise ValueError(f"mock provider was built for {self.timeframe}")
        self._advance()
        return self._closed.tail(count).reset_index(drop=True)

    def get_market_status(self) -> MarketStatus:
        return MarketStatus(True, "synthetic market is always open", self._sim_now)
