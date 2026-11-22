"""Market assistant: no look-ahead, detector geometry, report, robot gate / ASSISTANT mode, frames, bridge, API."""

import numpy as np
import pandas as pd
import pytest

from app.assistant.context import build, resample
from app.assistant.detectors import STRATEGIES, trend_following
from app.assistant.engine import Assistant
from app.assistant.live import gather_frames
from app.config import load_settings
from app.market.providers.mock_provider import MockProvider, generate_candles
from app.strategy.entry_engine import EntryDecision
from app.strategy.strategy import AdaptiveGridBasketStrategy


def walk(n=2500, seed=4, tf="15m", scenario="normal"):
    return generate_candles("XAUUSD", tf, n, scenario, seed=seed)


def frames_from_1m(days=30, seed=3):
    m1 = generate_candles("XAUUSD", "1m", 60 * 24 * days, "normal", seed=seed)
    return {"1m": m1.tail(3000), **{tf: resample(m1, tf) for tf in ("5m", "15m", "1h", "4h", "1d")}}


def test_context_has_no_lookahead():
    df = walk()
    full, cut = build(df, "15m"), build(df.iloc[:1800], "15m")
    cols = ["ema_fast", "atr", "adx", "rsi", "hi20", "lo20", "ema9"]
    pd.testing.assert_frame_equal(full.f[cols].iloc[:1800], cut.f[cols])
    pd.testing.assert_frame_equal(full.st.iloc[:1800], cut.st)
    assert (full.ph_conf[:1800] == cut.ph_conf).all() and (full.pl_conf[:1800] == cut.pl_conf).all()


@pytest.mark.parametrize("scenario", ["normal", "reversals", "sideways"])
def test_detectors_have_no_lookahead_and_sane_geometry(scenario):
    df = walk(1500, seed=7, scenario=scenario)
    full = build(df, "15m")
    found = 0
    for i in range(300, 1500, 37):
        cut = build(df.iloc[:i + 1], "15m")
        for s in STRATEGIES:
            a, b = s.detect(full, i, 0.54), s.detect(cut, i, 0.54)
            assert (a is None) == (b is None), (s.name, i)
            assert abs(s.fit(full, i) - s.fit(cut, i)) < 1e-9, (s.name, i)
            if a is not None:
                found += 1
                assert a.to_dict() == b.to_dict()
                assert (a.entry - a.stop) * a.direction > 0 and (a.target - a.entry) * a.direction > 0
    assert found > 0


def test_trend_following_reads_a_clear_uptrend():
    t = pd.date_range("2025-01-06", periods=600, freq="15min", tz="UTC")
    c = 2600 + np.arange(600) * 0.8 + np.sin(np.arange(600) / 3)
    df = pd.DataFrame({"timestamp": t, "open": c - 0.3, "high": c + 1.0, "low": c - 1.0, "close": c})
    sig = trend_following(build(df, "15m"), 599, 0.54)
    assert sig is not None and sig.direction == 1 and sig.stop < sig.entry < sig.target


def test_report_shape_and_fit_percentages():
    rep = Assistant().analyze(frames_from_1m(), 0.54, "15m")
    assert rep["action"] in ("BUY", "SELL", "WAIT") and 0 <= rep["confidence_pct"] <= 100
    assert sum(rep["strategy_fit_pct"].values()) == pytest.approx(100, abs=0.5)
    assert set(rep["timeframes"]) == {"1m", "5m", "15m", "1h", "4h"}       # 30 daily candles: too few for 1d
    assert rep["reasons"] and rep["disclaimer"]
    if rep["plan"]:
        p = rep["plan"]
        assert (p["entry"] - p["stop"]) * p["direction"] > 0
        assert "samples" in p["history"]


def test_history_uses_only_candles_before_now():
    df = walk(2200, seed=9)
    full, cut = build(df, "15m"), build(df.iloc[:1801], "15m")
    for s in STRATEGIES[:5]:
        a = Assistant().history(full, s.name, 1800, 0.54)
        b = Assistant().history(cut, s.name, 1800, 0.54)
        assert a == b, s.name


def test_confidence_threshold_forces_wait():
    rep = Assistant(min_confidence=1.01).analyze(frames_from_1m(), 0.54, "15m")
    assert rep["action"] == "WAIT"


def test_robot_gate_and_assistant_mode():
    st = AdaptiveGridBasketStrategy(load_settings(env={}))
    buy = EntryDecision("BUY", ["rules"], 0.8)
    assert st._with_assistant(buy).action == "BUY"                       # no report (backtest): gate not applied
    st.assistant_report = {"action": "SELL", "confidence_pct": 70.0, "reasons": ["4 strategies agree"]}
    held = st._with_assistant(buy)
    assert held.action == "WAIT" and "assistant says SELL" in held.reasons[0]
    st.assistant_report["action"] = "BUY"
    assert st._with_assistant(buy).action == "BUY"

    am = AdaptiveGridBasketStrategy(load_settings("assistant", env={}))
    assert am._with_assistant(EntryDecision("NO_SIGNAL", [])).action == "WAIT"          # waits for the analysis
    am.assistant_report = {"action": "SELL", "confidence_pct": 62.5, "reasons": ["x"]}
    d = am._with_assistant(EntryDecision("NO_SIGNAL", []))
    assert d.action == "SELL" and d.confidence == pytest.approx(0.625)
    am.assistant_report = {"action": "WAIT", "confidence_pct": 20.0, "reasons": ["confidence 20% below 55%"]}
    assert am._with_assistant(EntryDecision("BUY", [])).action == "WAIT"


def test_gather_frames_builds_higher_timeframes_from_the_feed():
    p = MockProvider("XAUUSD", "15m", "normal", seed=1, history_bars=800)
    fr = gather_frames(p, "15m")
    assert "15m" in fr and "1h" in fr and "4h" in fr and "1m" not in fr
    last15 = fr["15m"]["timestamp"].iloc[-1] + pd.Timedelta("15min")
    for tf, mins in (("1h", 60), ("4h", 240)):
        assert (fr[tf]["timestamp"] + pd.Timedelta(minutes=mins) <= last15).all()     # closed candles only
    until = fr["15m"]["timestamp"].iloc[-10]
    assert (gather_frames(p, "15m", until=until)["15m"]["timestamp"] + pd.Timedelta("15min") <= until).all()


def test_paper_runner_offers_the_assistant(tmp_path):
    from app.paper.runner import PaperRunner
    clock = [1000.0]
    p = MockProvider("XAUUSD", "15m", "reversals", seed=2, speed=60, clock=lambda: clock[0], history_bars=600)
    r = PaperRunner(load_settings("assistant", env={}, overrides={"market": {"history_bars": 600}}), p, None,
                    wall=lambda: clock[0])
    r.start()
    snap = r.assistant_snapshot()
    assert snap["drives_entries"] and snap["report"]["action"] in ("BUY", "SELL", "WAIT")
    for _ in range(200):
        clock[0] += 5
        r.step()
    assert r.robot.strategy.assistant_report is not None        # consulted before each entry decision
