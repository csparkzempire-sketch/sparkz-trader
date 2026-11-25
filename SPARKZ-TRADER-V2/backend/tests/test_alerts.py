"""Telegram alerts: which events are sent, repeat limiting, failures never reach the robot, wiring from env."""

from datetime import datetime, timezone

from app.engine.event_log import EventLog
from app.utils.alerts import Alerts


def make(clock=None, fail=False):
    out = []

    def send(text):
        if fail:
            raise RuntimeError("network down")
        out.append(text)
    a = Alerts(send, "SPARKZ [MT5 DEMO] XAUUSD", clock=clock or (lambda: 0.0), background=False)
    log = EventLog()
    log.listeners.append(a.on_event)
    return a, log, out


T = datetime(2026, 11, 25, tzinfo=timezone.utc)


def test_trades_are_sent_and_noise_is_not():
    a, log, out = make()
    log.add(T, "MARKET_ANALYZED", "x")
    log.add(T, "POSITION_OPENED", "B1: BUY 0.01 lots opened at 4300")
    log.add(T, "BASKET_CLOSED", "B1 closed: +3.20")
    assert len(out) == 2 and "buy 0.01" in out[0].lower() and "SPARKZ [MT5 DEMO]" in out[0]


def test_repeats_are_limited_but_trades_never():
    now = [0.0]
    a, log, out = make(clock=lambda: now[0])
    for _ in range(3):
        log.add(T, "DATA_STALE", "no tick for 130 s")
        log.add(T, "POSITION_OPENED", "B: BUY")
    assert sum("stale" in m for m in out) == 1 and sum("opened" in m for m in out) == 3
    now[0] += 301
    log.add(T, "DATA_STALE", "again")
    assert sum("stale" in m for m in out) == 2


def test_a_failed_send_never_reaches_the_robot():
    a, log, out = make(fail=True)
    log.add(T, "POSITION_OPENED", "B1")                     # must not raise
    assert a.failed == 1 and "network down" in a.last_error


def test_from_env_needs_both_values():
    assert Alerts.from_env("x", env={}) is None
    assert Alerts.from_env("x", env={"TELEGRAM_BOT_TOKEN": "t"}) is None
    a = Alerts.from_env("x", env={"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "TELEGRAM_ALERTS": "halt"})
    assert a.types == {"HALT"}
