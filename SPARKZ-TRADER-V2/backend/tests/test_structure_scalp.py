"""Market structure (swings, BOS/CHoCH, sweeps, Asia range) without look-ahead, and the scalping simulator."""

import numpy as np
import pandas as pd
import pytest

from app.research import scalp
from app.research.scalp import ScalpParams
from app.research.structure import asia_range, pivots, structure, sweeps

T0 = pd.Timestamp("2025-01-06 08:00", tz="UTC")       # a Monday, London session


def candles(prices, freq="1min", t0=T0, wick=0.0):
    p = np.asarray(prices, float)
    return pd.DataFrame({"timestamp": pd.date_range(t0, periods=len(p), freq=freq), "open": p,
                         "high": p + wick, "low": p - wick, "close": p, "volume": 1.0})


ZIGZAG = [10, 11, 12, 11, 10, 11, 13, 14, 12, 9, 8, 9, 10, 11, 12, 15, 13, 16]


def test_pivots():
    ph, pl = pivots(np.array([1, 2, 5, 2, 1, 2, 3.0]), np.array([1, 2, 5, 2, 1, 2, 3.0]), 2)
    assert ph.tolist() == [False, False, True, False, False, False, False]
    assert pl.tolist() == [False, False, False, False, True, False, False]


def test_swing_is_only_known_after_its_right_hand_bars():
    st = structure(candles(ZIGZAG), n=1)
    assert np.isnan(st["sh"].iloc[2]) and st["sh"].iloc[3] == 12        # pivot at bar 2, confirmed at bar 3


def test_bos_and_choch():
    st = structure(candles(ZIGZAG), n=1)
    events = {i: e for i, e in enumerate(st["event"]) if e}
    assert events == {6: "CHOCH_UP", 9: "CHOCH_DOWN", 15: "CHOCH_UP", 17: "BOS_UP"}
    assert st["trend"].iloc[8] == 1 and st["trend"].iloc[12] == -1 and st["trend"].iloc[17] == 1


def test_structure_has_no_lookahead():
    rng = np.random.default_rng(3)
    df = candles(2000 + rng.normal(0, 1, 800).cumsum(), wick=0.3)
    full, cut = structure(df, 3), structure(df.iloc[:500], 3)
    pd.testing.assert_frame_equal(full.iloc[:500].reset_index(drop=True), cut)


def test_sweep_low_needs_wick_through_and_close_back():
    df = candles([10, 11, 12, 11, 10, 11, 12, 11.5, 11.0])
    df.loc[8, "low"] = 9.5                                # wick below the swing low (10), close 11 back above
    st = structure(df, 1)
    sw = sweeps(df, st)
    assert sw["sweep_low"].tolist()[-1] and not sw["sweep_low"].iloc[:-1].any()


def test_asia_range_is_known_only_after_asia():
    df = candles(np.arange(10.0, 10.0 + 9 * 60), t0=pd.Timestamp("2025-01-06 00:00", tz="UTC"), wick=0.5)
    ar = asia_range(df)
    assert ar["asia_high"].iloc[:7 * 60].isna().all()
    assert ar["asia_high"].iloc[7 * 60] == pytest.approx(10.0 + 7 * 60 - 1 + 0.5)
    assert ar["asia_low"].iloc[7 * 60] == pytest.approx(9.5)


def test_htf_bias_uses_only_closed_higher_candles():
    htf = candles(ZIGZAG, freq="15min")
    trend = structure(htf, 1)["trend"].to_numpy()
    j = 6                                                 # CHOCH_UP on the 15m candle starting at bar 6
    setup = candles(np.full(15 * len(ZIGZAG), 1.0))
    bias = scalp.htf_bias(setup, htf, "1m", "15m", 1)
    start = 15 * j                                        # 1m candles inside 15m candle j
    assert (bias[start:start + 14] == trend[j - 1]).all()   # candle j not closed yet
    assert bias[start + 14] == trend[j] == 1                # the 1m candle closing with it sees it


def one_trade(prices_ohlc, stop, d=1, **kw):
    m1 = pd.DataFrame(prices_ohlc, columns=["open", "high", "low", "close"])
    m1.insert(0, "timestamp", pd.date_range(T0, periods=len(m1), freq="1min"))
    sig = pd.DataFrame({"time": [T0], "dir": [d], "stop": [stop]})
    p = ScalpParams(spread=0.5, slippage=0.1, rr=2.0, min_risk_spreads=1.0, **kw)
    return scalp.simulate(m1, sig, p), p


def test_long_target_with_costs():
    tr, p = one_trade([(100, 100, 100, 100), (100.5, 106, 100.5, 105)], stop=98.35)
    t = tr.iloc[0]
    assert t["entry"] == pytest.approx(100.35)            # open + half spread + slippage
    assert t["risk"] == pytest.approx(2.0) and t["target"] == pytest.approx(104.35)
    assert t["reason"] == "TARGET" and t["r"] == pytest.approx(2.0)
    assert t["gross_r"] == pytest.approx(2.0 + (0.5 + 0.1) / 2.0)
    assert t["pnl_usd"] == pytest.approx(2.0 * p.risk_usd)


def test_stop_and_target_in_one_candle_counts_as_stop():
    tr, _ = one_trade([(100, 100, 100, 100), (100, 110, 90, 100)], stop=98.35)
    t = tr.iloc[0]
    assert t["reason"] == "STOP" and t["ambiguous"]
    assert t["exit"] == pytest.approx(98.25) and t["r"] == pytest.approx((98.25 - 100.35) / 2.0)


def test_short_and_gap_through_stop():
    tr, _ = one_trade([(100, 100, 100, 100), (104, 104, 104, 104)], stop=101.65, d=-1)
    t = tr.iloc[0]
    assert t["entry"] == pytest.approx(99.65)             # bid - slippage
    assert t["reason"] == "STOP_GAP" and t["exit"] == pytest.approx(104.25 + 0.1)


def test_risk_too_small_for_the_spread_is_skipped():
    m1 = candles([100, 100, 100])                         # entry 100.30, risk 0.40 < 2 x 0.5 spread
    sig = pd.DataFrame({"time": [T0], "dir": [1], "stop": [99.9]})
    assert scalp.simulate(m1, sig, ScalpParams(spread=0.5)).empty


def test_signals_on_random_walk_respect_session_and_bias():
    rng = np.random.default_rng(1)
    m1 = candles(2600 + rng.normal(0, 0.4, 3 * 24 * 60).cumsum(), t0=pd.Timestamp("2025-01-06", tz="UTC"), wick=0.3)
    for variant in ("choch", "sweep_choch"):
        sig = scalp.signals(m1, ScalpParams(tf="1m", swing_n=2, variant=variant))
        hours = (pd.to_datetime(sig["time"], utc=True) - pd.Timedelta(seconds=1)).dt.hour
        assert len(sig) and ((hours >= 7) & (hours < 20)).all()
    with pytest.raises(ValueError):
        scalp.signals(m1, ScalpParams(variant="nope"))
