"""Paper loop: mock feed -> same Robot -> SQLite; stale data blocks entries; provider errors never crash it."""

import pytest

from app.config import load_settings
from app.database.repository import Repository
from app.market.providers.base import ProviderError
from app.market.providers.mock_provider import MockProvider
from app.paper.runner import PaperRunner


@pytest.fixture
def clock():
    return [1_000.0]


def make(clock, tmp_path, scenario="reversals"):
    w = lambda: clock[0]  # noqa: E731
    p = MockProvider("XAUUSD", "15m", scenario, seed=2, speed=60, clock=w, history_bars=300)
    repo = Repository(f"sqlite:///{tmp_path / 't.db'}")
    r = PaperRunner(load_settings(env={}, overrides={"market": {"history_bars": 300}}), p, repo, wall=w)
    r.start()
    return r, p, repo


def run(r, clock, steps, dt=5.0):
    for _ in range(steps):
        clock[0] += dt
        r.step()


def test_paper_loop_trades_and_persists(clock, tmp_path):
    r, _, repo = make(clock, tmp_path)
    run(r, clock, 600)                     # 600 x 5 s x 60 = 50 hours of simulated market
    assert r.robot.bar >= 190
    c = repo.counts(r.run_id)
    assert c["events"] > 0 and c["baskets"] == len(r.robot.completed)
    snap = r.dashboard()
    assert snap["account"]["label"] == "PAPER ACCOUNT"
    assert snap["health"]["executor"]["live_trading_enabled"] is False
    assert snap["health"]["overall"] in ("OK", "DEGRADED")
    if r.robot.completed:
        assert repo.baskets(r.run_id)[-1]["uid"] == r.robot.completed[-1].uid


def test_stale_data_blocks_entries_and_recovers(clock, tmp_path):
    r, p, _ = make(clock, tmp_path)
    run(r, clock, 5)
    real = p._advance
    p._advance = lambda: None              # feed freezes
    run(r, clock, 30)
    assert not r.robot.data_ok and "unchanged" in r.robot.data_reason
    assert r.health()["overall"] == "STALE"
    p._advance = real
    run(r, clock, 2)
    assert r.robot.data_ok


def test_provider_errors_are_logged_not_fatal(clock, tmp_path):
    r, p, _ = make(clock, tmp_path)

    def boom():
        raise ProviderError("connection refused")
    p.get_latest_tick = boom
    run(r, clock, 40)
    assert r.errors >= 40
    assert sum(1 for e in r.robot.log.entries if e.type == "ERROR") <= 6      # throttled
    assert not r.robot.data_ok
