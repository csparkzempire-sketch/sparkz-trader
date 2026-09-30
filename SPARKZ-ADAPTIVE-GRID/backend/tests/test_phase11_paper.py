"""Phase 11: paper trading runs the same engine forward, persists state, and never trades live."""

from __future__ import annotations

import pandas as pd
import pytest

from app.backtest.runner import run_backtest
from app.config import load_config
from app.models.database import CompletedBasket, EquitySnapshot, PaperAccount, session_factory
from app.paper.broker import LiveTradingDisabled, SimulatedBroker, get_broker
from app.paper.simulator import create_account, list_accounts, resume, status, step
from tests.conftest import make_candles

ENV = {"GRID_MODE": "ATR", "COOLDOWN_BARS_AFTER_LOSS": "2"}


def test_broker_fails_closed():
    s = load_config(env={})
    assert isinstance(get_broker(s), SimulatedBroker)
    with pytest.raises(LiveTradingDisabled):
        get_broker(s, live=True)


def test_paper_matches_a_backtest_over_the_same_bars():
    """Stepping bar by bar (several steps) gives the same baskets as one backtest over those bars."""
    s = load_config(env=ENV)
    data = make_candles(3200, drift=0.02, vol=1.5, seed=31)
    create_account("p1", s)
    start = 2400
    step("p1", candles=data.iloc[:start], now=pd.Timestamp("2030-01-01", tz="UTC"))
    for end in range(start + 100, len(data) + 1, 150):
        step("p1", candles=data.iloc[:end], now=pd.Timestamp("2030-01-01", tz="UTC"))
    step("p1", candles=data, now=pd.Timestamp("2030-01-01", tz="UTC"))

    # the same engine over the same bars, one pass: first processed bar is start-1 (the account's first analysis)
    s2 = s.model_copy(update={"market": s.market.model_copy(update={"start": data["timestamp"].iloc[start - 1].isoformat()})})
    bt = run_backtest(s2, candles=data.iloc[-3000:].reset_index(drop=True))
    with session_factory()() as ses:
        acct = ses.query(PaperAccount).filter_by(name="p1").one()
        paper = ses.query(CompletedBasket).filter_by(paper_account_id=acct.id).order_by(CompletedBasket.closed_at).all()
    closed_bt = [b for b in bt.baskets if b.close_reason != "END_OF_DATA"]
    assert len(paper) == len(closed_bt) > 3
    for p, b in zip(paper, closed_bt):
        assert p.basket_uid == b.uid and p.pnl == pytest.approx(b.pnl) and p.positions == b.positions


def test_repeated_steps_without_new_bars_change_nothing():
    s = load_config(env=ENV)
    data = make_candles(2600, seed=32)
    create_account("p2", s)
    now = pd.Timestamp("2030-01-01", tz="UTC")
    step("p2", candles=data, now=now)
    first = status("p2")
    r = step("p2", candles=data, now=now)
    assert r["bars_processed"] == 0 and status("p2")["balance"] == first["balance"]


def test_first_step_does_not_backfill_history():
    s = load_config(env=ENV)
    create_account("p3", s)
    r = step("p3", candles=make_candles(2600, seed=33), now=pd.Timestamp("2030-01-01", tz="UTC"))
    assert r["bars_processed"] == 1 and r["closed_baskets"] == []
    with session_factory()() as ses:
        assert ses.query(EquitySnapshot).count() == 1


def test_duplicate_accounts_and_listing():
    s = load_config(env=ENV)
    create_account("p4", s)
    with pytest.raises(ValueError, match="already exists"):
        create_account("p4", s)
    assert [a["name"] for a in list_accounts()] == ["p4"]


def test_resume_is_explicit():
    s = load_config(env={**ENV, "MAX_ACCOUNT_DRAWDOWN_PERCENT": "0.2", "BASE_LOT": "0.5"})
    data = make_candles(3000, drift=-0.3, vol=2.0, seed=34)
    create_account("p5", s)
    step("p5", candles=data.iloc[:2500], now=pd.Timestamp("2030-01-01", tz="UTC"))
    r = step("p5", candles=data, now=pd.Timestamp("2030-01-01", tz="UTC"))
    assert r["halted"]
    again = step("p5", candles=data, now=pd.Timestamp("2030-01-01", tz="UTC"))
    assert again["halted"]                         # stays halted on its own
    assert resume("p5") == "resumed" and status("p5")["halted"] is None
