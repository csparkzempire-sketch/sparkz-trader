from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from app.paper.scheduler import PaperFeedScheduler, FeedStatus
from app.paper.simulator import PaperAccountState, PaperTradingSimulator


@pytest.fixture
def account():
    a = PaperAccountState(balance=10_000)
    a.is_active = True
    return a


def test_tick_opens_position_on_new_buy_signal(monkeypatch, synthetic_ohlcv, account):
    import app.paper.scheduler as sched_mod

    monkeypatch.setattr(sched_mod, "download_ohlcv", lambda symbol, timeframe: synthetic_ohlcv.copy())
    monkeypatch.setattr(sched_mod, "baseline_signal", lambda df: pd.Series(["BUY"] * len(df), index=df.index))

    scheduler = PaperFeedScheduler(PaperTradingSimulator())
    status = FeedStatus(
        account_name="acct", symbol="EURUSD=X", timeframe="1h", strategy="baseline",
        poll_interval_seconds=60, running=True,
    )

    asyncio.run(scheduler._tick(account, status))

    assert "EURUSD=X" in account.open_positions
    assert status.last_signal == "BUY"
    assert status.ticks_processed == 1
    assert status.last_error is None


def test_tick_does_not_reopen_when_position_already_open(monkeypatch, synthetic_ohlcv, account):
    import app.paper.scheduler as sched_mod

    monkeypatch.setattr(sched_mod, "download_ohlcv", lambda symbol, timeframe: synthetic_ohlcv.copy())
    monkeypatch.setattr(sched_mod, "baseline_signal", lambda df: pd.Series(["BUY"] * len(df), index=df.index))

    sim = PaperTradingSimulator()
    scheduler = PaperFeedScheduler(sim)
    status = FeedStatus(
        account_name="acct", symbol="EURUSD=X", timeframe="1h", strategy="baseline",
        poll_interval_seconds=60, running=True,
    )

    asyncio.run(scheduler._tick(account, status))
    first_size = account.open_positions["EURUSD=X"].size

    asyncio.run(scheduler._tick(account, status))
    # Still just one position, unchanged (second tick sees the same "latest"
    # candle since we didn't advance the fake data, so is_new_candle is False).
    assert len(account.open_positions) == 1
    assert account.open_positions["EURUSD=X"].size == first_size


def test_tick_checks_stop_target_on_open_position(monkeypatch, synthetic_ohlcv, account):
    import app.paper.scheduler as sched_mod

    sim = PaperTradingSimulator()
    scheduler = PaperFeedScheduler(sim)

    # Manually open a position first.
    sim.open_position(account, "EURUSD=X", "BUY", raw_price=1.10, atr_value=0.0010)
    pos = account.open_positions["EURUSD=X"]

    # Craft a valid candle (low <= open/close <= high) whose low breaches
    # the stop but whose high stays well below the target, so only the
    # stop triggers.
    df = synthetic_ohlcv.copy()
    last_idx = df.index[-1]
    df.loc[last_idx, "open"] = pos.entry_price
    df.loc[last_idx, "close"] = pos.entry_price
    df.loc[last_idx, "low"] = pos.stop_price - 0.0005
    df.loc[last_idx, "high"] = pos.entry_price + 0.0003

    monkeypatch.setattr(sched_mod, "download_ohlcv", lambda symbol, timeframe: df)
    monkeypatch.setattr(sched_mod, "baseline_signal", lambda d: pd.Series(["HOLD"] * len(d), index=d.index))

    status = FeedStatus(
        account_name="acct", symbol="EURUSD=X", timeframe="1h", strategy="baseline",
        poll_interval_seconds=60, running=True,
    )
    asyncio.run(scheduler._tick(account, status))

    assert "EURUSD=X" not in account.open_positions
    assert len(account.trade_history) == 1
    assert account.trade_history[0].reason == "STOP"


def test_scheduler_start_rejects_duplicate_feed(account):
    sim = PaperTradingSimulator()
    scheduler = PaperFeedScheduler(sim)

    async def _run():
        scheduler.start(account, "acct", "EURUSD=X", "1h", poll_interval_seconds=9999)
        with pytest.raises(RuntimeError):
            scheduler.start(account, "acct", "EURUSD=X", "1h", poll_interval_seconds=9999)
        scheduler.stop("acct")

    asyncio.run(_run())


def test_scheduler_stop_is_idempotent(account):
    sim = PaperTradingSimulator()
    scheduler = PaperFeedScheduler(sim)
    scheduler.stop("never_started")  # should not raise
