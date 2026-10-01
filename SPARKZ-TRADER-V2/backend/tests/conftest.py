"""Shared helpers: hand-built market states and a robot driven tick by tick."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.config import load_settings
from app.engine.robot import Robot
from app.market.market_engine import MarketState
from app.market.providers.base import Tick

T0 = datetime(2025, 1, 6, 8, 0, tzinfo=timezone.utc)   # a Monday


def make_state(price: float = 2000.0, direction: str = "BUY", atr: float = 4.0, t: datetime = T0,
               **kw) -> MarketState:
    """A ready 15m XAUUSD state whose rules clearly agree on `direction` (or on nothing: direction=None)."""
    up = direction == "BUY"
    flat = direction is None
    st = MarketState(
        symbol="XAUUSD", timestamp=t, bar_time=t, bid=price - 0.15, ask=price + 0.15, mid=price, spread=0.30,
        open=price, high=price + 1, low=price - 1, close=price, atr=atr,
        rsi=50.0 if flat else (60.0 if up else 40.0),
        ema20=price if flat else (price - 2 if up else price + 2),
        ema50=price if flat else (price - 5 if up else price + 5),
        ema200=price if flat else (price - 20 if up else price + 20),
        macd=0.0 if flat else (1.0 if up else -1.0), macd_signal=0.0, macd_hist=0.0 if flat else (1.0 if up else -1.0),
        adx=10.0 if flat else 30.0, plus_di=25.0 if up else 15.0, minus_di=15.0 if up else 25.0,
        bb_upper=price + 8, bb_lower=price - 8, bb_mid=price, volatility=0.001, vol_percentile=0.5,
        ret_recent=0.0 if flat else (0.01 if up else -0.01),
        regime="RANGING" if flat else ("TRENDING_UP" if up else "TRENDING_DOWN"),
        trend_regime="RANGING" if flat else ("TRENDING_UP" if up else "TRENDING_DOWN"),
        vol_regime="NORMAL_VOLATILITY", ready=True)
    return replace(st, **kw)


class Driver:
    """Feeds hand-made bar states and ticks to a real Robot (strategy + risk + executor + account)."""

    def __init__(self, settings=None, **overrides):
        self.s = settings or load_settings(env={}, overrides=overrides or None)
        self.robot = Robot(self.s, mode="BACKTEST")
        self.t = T0
        self._state: MarketState | None = None
        m = self.robot.market

        def on_close(candle, index=None):
            m.last_bar = self._state
            return self._state
        m.on_candle_close = on_close

    def bar(self, price: float, direction: str | None = "BUY", **kw) -> MarketState:
        self.t += timedelta(minutes=15)
        self._state = make_state(price, direction, t=self.t - timedelta(minutes=15), **kw)
        self.robot.process_bar_close({})
        return self._state

    def tick(self, mid: float, gap: bool = False, seconds: int = 1) -> None:
        self.t += timedelta(seconds=seconds)
        self.robot.process_tick(Tick("XAUUSD", self.t, mid - 0.15, mid + 0.15, gap=gap))

    def path(self, *mids: float) -> None:
        for p in mids:
            self.tick(p)

    @property
    def basket(self):
        return self.robot.strategy.basket

    def types(self) -> list[str]:
        return [e.type for e in self.robot.log.entries]


@pytest.fixture
def driver():
    return Driver
