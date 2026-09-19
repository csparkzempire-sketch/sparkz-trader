"""
Paper trading live feed scheduler.

Runs a background polling loop per account that:
  1. Downloads and validates the latest market data for the configured symbol/timeframe.
  2. Computes features and a signal (baseline rules, or a trained model's probability).
  3. If a position is open, checks the newest completed candle's high/low
     against the stop/target (same conservative stop-wins-ties assumption as
     the backtest engine).
  4. If no position is open and the signal is BUY/SELL, opens one.

This is polling-based (not a websocket tick feed) and only ever acts on
CLOSED candles — it never uses an in-progress candle's high/low, which
would be look-ahead in exactly the way the backtest engine's execution-
timing rule guards against. Network I/O runs in a thread via
`asyncio.to_thread` so it never blocks the FastAPI event loop.

No real broker code exists here or anywhere in this module.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from app.config import Settings, settings
from app.data.downloader import DownloadError, download_ohlcv
from app.data.validator import DataValidationError, validate_and_clean
from app.features.feature_engineering import build_feature_matrix, get_feature_columns
from app.ml.dataset import add_labels
from app.ml.model_registry import load_model_artifact
from app.paper.simulator import PaperAccountState, PaperTradingSimulator
from app.strategy.rules import baseline_signal
from app.strategy.signals import signal_from_probability
from app.utils.logging import get_logger, kv

logger = get_logger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 60.0


@dataclass
class FeedStatus:
    account_name: str
    symbol: str
    timeframe: str
    strategy: str
    poll_interval_seconds: float
    running: bool
    last_poll_at: datetime | None = None
    last_candle_timestamp: datetime | None = None
    last_signal: str | None = None
    last_error: str | None = None
    ticks_processed: int = 0


@dataclass
class _FeedHandle:
    task: asyncio.Task
    status: FeedStatus


class PaperFeedScheduler:
    """
    Owns one background asyncio task per paper account that has a live feed
    started. Call `stop_all()` from the FastAPI shutdown/lifespan hook so
    tasks don't leak across reloads.
    """

    def __init__(self, simulator: PaperTradingSimulator, cfg: Settings | None = None):
        self.simulator = simulator
        self.cfg = cfg or settings
        self._feeds: dict[str, _FeedHandle] = {}
        self._last_seen_timestamp: dict[str, datetime] = {}

    def is_running(self, account_name: str) -> bool:
        return account_name in self._feeds and not self._feeds[account_name].task.done()

    def status(self, account_name: str) -> FeedStatus | None:
        handle = self._feeds.get(account_name)
        return handle.status if handle else None

    def start(
        self,
        account: PaperAccountState,
        account_name: str,
        symbol: str,
        timeframe: str,
        strategy: str = "baseline",
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> FeedStatus:
        if self.is_running(account_name):
            raise RuntimeError(f"A live feed is already running for account '{account_name}'. Stop it first.")

        status = FeedStatus(
            account_name=account_name,
            symbol=symbol,
            timeframe=timeframe,
            strategy=strategy,
            poll_interval_seconds=poll_interval_seconds,
            running=True,
        )
        task = asyncio.create_task(self._run_loop(account, status))
        self._feeds[account_name] = _FeedHandle(task=task, status=status)
        logger.info("Paper feed started %s", kv(account_name=account_name, symbol=symbol, timeframe=timeframe, strategy=strategy))
        return status

    def stop(self, account_name: str) -> None:
        handle = self._feeds.pop(account_name, None)
        if handle is None:
            return
        handle.status.running = False
        handle.task.cancel()
        logger.info("Paper feed stopped %s", kv(account_name=account_name))

    def stop_all(self) -> None:
        for name in list(self._feeds.keys()):
            self.stop(name)

    async def _run_loop(self, account: PaperAccountState, status: FeedStatus) -> None:
        try:
            while True:
                await self._tick(account, status)
                await asyncio.sleep(status.poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a single bad tick must not kill the loop silently
            status.last_error = str(exc)
            logger.info("Paper feed tick failed %s", kv(account_name=status.account_name, error=str(exc)))
            status.running = False
            raise

    async def _tick(self, account: PaperAccountState, status: FeedStatus) -> None:
        from app.utils.time import utc_now

        status.last_poll_at = utc_now()
        try:
            raw = await asyncio.to_thread(download_ohlcv, status.symbol, status.timeframe)
            clean, _report = await asyncio.to_thread(validate_and_clean, raw, status.timeframe)
        except (DownloadError, DataValidationError) as exc:
            status.last_error = str(exc)
            return

        featured = build_feature_matrix(clean, self.cfg)
        featured = featured.dropna(subset=["atr"])
        if featured.empty:
            status.last_error = "Not enough warmed-up data yet."
            return

        latest = featured.iloc[-1]
        latest_ts = latest["timestamp"].to_pydatetime()

        # Only act once per NEW closed candle — never re-process the same
        # bar, and never act on a bar that might still be forming.
        seen = self._last_seen_timestamp.get(status.account_name)
        is_new_candle = seen is None or latest_ts > seen

        # First, always check stop/target for any open position against the
        # latest closed candle's high/low (safe to do even if we've already
        # acted on this candle's signal, since it's idempotent per position).
        if status.symbol in account.open_positions:
            self.simulator.check_and_close_if_hit(
                account, status.symbol, high=float(latest["high"]), low=float(latest["low"]), timestamp=latest_ts
            )

        if is_new_candle:
            signal, probability = self._compute_signal(featured, status)
            status.last_signal = signal
            status.last_candle_timestamp = latest_ts
            status.ticks_processed += 1
            self._last_seen_timestamp[status.account_name] = latest_ts

            if status.symbol not in account.open_positions and signal in ("BUY", "SELL"):
                self.simulator.open_position(
                    account,
                    symbol=status.symbol,
                    direction=signal,
                    raw_price=float(latest["close"]),
                    atr_value=float(latest["atr"]),
                    timestamp=latest_ts,
                )
        status.last_error = None

    def _compute_signal(self, featured, status: FeedStatus) -> tuple[str, float | None]:
        if status.strategy == "baseline":
            sig = baseline_signal(featured).iloc[-1]
            return sig, None

        try:
            model = load_model_artifact(status.strategy)
        except FileNotFoundError:
            status.last_error = f"Unknown strategy/model_id: {status.strategy}"
            return "HOLD", None

        labeled = add_labels(featured, cfg=self.cfg)
        feature_cols = get_feature_columns(labeled)
        row = labeled[feature_cols].iloc[[-1]]
        if row.isna().any(axis=1).iloc[0]:
            return "HOLD", None

        proba_up = float(model.predict_proba(row)[0][1])
        result = signal_from_probability(proba_up, cfg=self.cfg)
        return result.signal, proba_up


# Module-level singleton bound to the shared simulator (app.paper.state), so
# positions/trades it opens are visible through the same /paper/* routes a
# user queries manually.
from app.paper import state as _paper_state

paper_feed_scheduler = PaperFeedScheduler(_paper_state.simulator)
