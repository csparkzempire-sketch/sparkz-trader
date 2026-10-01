"""
Paper trading loop: live (or synthetic) market data into the SAME Robot the
backtester uses. Every order is simulated; the account is a PAPER ACCOUNT.

Each step (every `market.poll_seconds`):
  1. read the latest tick and the market status from the provider;
  2. feed any candles that closed since the last step to the robot, in order
     (the strategy decides only on closed candles);
  3. feed the tick (marked as a discrete poll: a level the price jumped over
     since the previous poll fills at the current price, not at the level);
  4. check data freshness: if the price has not updated for
     `market.stale_after_seconds`, or a live feed's last tick is that old, or
     the market is closed, new positions are blocked until fresh data returns.
     An open basket keeps being managed (its target and loss limit still work
     on whatever prices arrive).

Provider errors are counted and logged (at most once a minute per message)
and never crash the loop.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from app.backtest.metrics import clean
from app.config import Settings
from app.database.repository import Repository
from app.engine.health import build_health
from app.engine.robot import Robot
from app.market.providers.base import MarketDataProvider, ProviderError


class PaperRunner:
    def __init__(self, settings: Settings, provider: MarketDataProvider, repo: Repository | None = None,
                 wall: Callable[[], float] = time.time, label: str = "paper"):
        self.s, self.provider, self.repo, self.wall = settings, provider, repo, wall
        self.robot = Robot(settings, mode="PAPER")
        self.info = provider.info()
        self.run_id: int | None = None
        self.label = label
        self.last_bar_ts: pd.Timestamp | None = None
        self.last_tick = None
        self.last_change_wall: float | None = None
        self.market_open: bool | None = None
        self.errors = 0
        self.last_error: str | None = None
        self._err_seen: dict[str, float] = {}
        self.started_at = wall()
        self.running = False
        self._stop = False
        self.listeners: list[Callable[[dict], None]] = []
        self.lock = threading.RLock()      # the loop thread, the API and the WebSocket share the robot

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        if self.repo is not None:
            self.run_id = self.repo.start_run("PAPER", self.s, self.label, self.info.name)
            self.repo.attach(self.robot, self.run_id)
        now = datetime.now(timezone.utc)
        self.robot.log.add(now, "STARTED", f"Paper trading started: {self.s.market.symbol} {self.s.market.timeframe}, "
                           f"data from {self.info.name}{' (delayed)' if self.info.delayed else ''}. "
                           "PAPER ACCOUNT: all orders are simulated.", {"provider": self.info.name,
                                                                         "notes": self.info.notes})
        try:
            hist = self.provider.get_candles(self.s.market.timeframe, self.s.market.history_bars)
            self.robot.warm_up(hist)
            if len(hist):
                self.last_bar_ts = pd.Timestamp(hist["timestamp"].iloc[-1])
        except Exception as e:
            self._error(f"history: {e}")

    # ------------------------------------------------------------------ one step
    def step(self) -> None:
        with self.lock:
            self._step()
        if self.listeners:
            snap = self.dashboard()
            for fn in list(self.listeners):
                try:
                    fn(snap)
                except Exception:
                    pass

    def _step(self) -> None:
        now_wall = self.wall()
        try:
            st = self.provider.get_market_status()
            self.market_open = st.open
        except Exception as e:
            self._error(f"market status: {e}")
        try:
            candles = self.provider.get_candles(self.s.market.timeframe, 5)
            for _, row in candles.iterrows():
                ts = pd.Timestamp(row["timestamp"])
                if self.last_bar_ts is None or ts > self.last_bar_ts:
                    self.last_bar_ts = ts
                    st = self.robot.process_bar_close(row)
                    if st is not None and self.repo is not None and self.run_id is not None and self.robot.equity_curve:
                        self.repo.save_equity(self.run_id, self.robot.equity_curve[-1])
        except Exception as e:
            self._error(f"candles: {e}")
        try:
            tick = self.provider.get_latest_tick()
            if self.last_tick is None or (tick.time, tick.bid, tick.ask) != (self.last_tick.time, self.last_tick.bid,
                                                                           self.last_tick.ask):
                self.last_change_wall = now_wall
                self.last_tick = tick
                self.robot.process_tick(replace(tick, gap=True))
        except Exception as e:
            self._error(f"tick: {e}")
        self._check_freshness(now_wall)

    def _check_freshness(self, now_wall: float) -> None:
        limit = self.s.market.stale_after_seconds
        when = self.last_tick.time if self.last_tick else datetime.now(timezone.utc)
        reason = ""
        if self.last_tick is None:
            reason = "no price received yet"
        elif self.market_open is False:
            reason = "market closed"
        elif self.last_change_wall is not None and now_wall - self.last_change_wall > limit:
            reason = f"price unchanged for {now_wall - self.last_change_wall:.0f} s (limit {limit} s)"
        elif self.info.live and not self.info.delayed:
            age = (datetime.now(timezone.utc) - self.last_tick.time).total_seconds()
            if age > limit:
                reason = f"last tick is {age:.0f} s old (limit {limit} s)"
        self.robot.set_data_status(not reason, reason or "fresh", when)

    def _error(self, msg: str) -> None:
        self.errors += 1
        self.last_error = msg
        now = self.wall()
        key = msg[:80]
        if now - self._err_seen.get(key, -1e9) >= 60:
            self._err_seen[key] = now
            t = self.last_tick.time if self.last_tick else datetime.now(timezone.utc)
            self.robot.log.add(t, "ERROR", f"Market data error: {msg}")

    # ------------------------------------------------------------------ loop
    async def run(self) -> None:
        self.running, self._stop = True, False
        try:
            while not self._stop:
                await asyncio.to_thread(self.step)
                await asyncio.sleep(self.s.market.poll_seconds)
        finally:
            self.running = False

    def stop_loop(self) -> None:
        self._stop = True
        if self.repo is not None and self.run_id is not None:
            self.repo.end_run(self.run_id, self.robot.account.snapshot())

    # ------------------------------------------------------------------ views
    def health(self) -> dict:
        if self.last_tick is None:
            age = None
        elif self.info.live:
            age = (datetime.now(timezone.utc) - self.last_tick.time).total_seconds()
        else:   # synthetic / replayed feeds run on their own clock: age = time since the price last changed
            age = self.wall() - self.last_change_wall if self.last_change_wall is not None else None
        return build_health(self.robot, self.info, self.market_open, self.last_tick.time if self.last_tick else None,
                            age, self.s.market.stale_after_seconds, self.errors, self.last_error, self.repo,
                            self.started_at, self.running)

    def dashboard(self, events: int = 100) -> dict:
        with self.lock:
            return clean(self._dashboard(events))

    def _dashboard(self, events: int) -> dict:
        r = self.robot
        curve = r.equity_curve[-500:]
        return {**r.snapshot(), "health": self.health(), "events": r.log.tail(events),
                "equity_curve": [{"time": p.time.isoformat(), "equity": p.equity, "balance": p.balance}
                                 for p in curve],
                "recent_baskets": [b.to_dict() for b in r.completed[-20:]],
                "candles": self._candles(),
                "provider": {"name": self.info.name, "live": self.info.live, "delayed": self.info.delayed,
                             "notes": self.info.notes}}

    def _candles(self, n: int = 120) -> list[dict]:
        c = self.robot.market.candles.tail(n)
        return [{"time": pd.Timestamp(row.timestamp).isoformat(), "open": float(row.open), "high": float(row.high),
                 "low": float(row.low), "close": float(row.close)} for row in c.itertuples()]
