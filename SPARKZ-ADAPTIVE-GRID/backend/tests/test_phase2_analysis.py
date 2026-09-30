"""Phases 2-3: indicators, look-ahead safety, regimes and entry rules."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.config import AnalysisCfg, EntryCfg, EntryMode
from app.strategy.entry_engine import evaluate_entry, still_supports
from app.strategy.market_analysis import atr, compute_features, ema, rsi, trailing_percentile
from app.strategy.regime_detector import label_regimes
from tests.conftest import make_candles

CFG = AnalysisCfg(vol_lookback=200)


def test_ema_matches_the_recursive_definition():
    s = pd.Series(np.arange(1.0, 31.0))
    e = ema(s, 10)
    k = 2 / 11
    manual = s.iloc[0]
    for x in s.iloc[1:]:
        manual = k * x + (1 - k) * manual
    assert e.iloc[-1] == pytest.approx(manual) and e.iloc[:9].isna().all()


def test_rsi_extremes_and_balance():
    assert rsi(pd.Series(np.arange(1.0, 40.0)), 14).iloc[-1] == pytest.approx(100.0)
    assert rsi(pd.Series(np.arange(40.0, 1.0, -1)), 14).iloc[-1] == pytest.approx(0.0)
    alt = pd.Series([100 + (i % 2) for i in range(200)], dtype=float)
    assert rsi(alt, 14).iloc[-1] == pytest.approx(50.0, abs=2)


def test_atr_of_constant_range_bars():
    df = pd.DataFrame({"open": [10.0] * 50, "high": [11.0] * 50, "low": [9.0] * 50, "close": [10.0] * 50})
    assert atr(df, 14).iloc[-1] == pytest.approx(2.0)


def test_trailing_percentile():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 0.5])
    p = trailing_percentile(s, 5, 1)
    assert p.iloc[3] == pytest.approx(1.0) and p.iloc[4] == pytest.approx(0.2)


def test_features_never_use_future_bars():
    """Row N computed on the full history equals row N computed on data truncated at N."""
    df = make_candles(700, drift=0.05)
    full = compute_features(df, CFG)
    cols = [c for c in full.columns if c != "timestamp"]
    for n in (250, 400, 699):
        part = compute_features(df.iloc[: n + 1], CFG)
        a, b = full.iloc[n][cols].astype(float), part.iloc[n][cols].astype(float)
        assert np.allclose(a.to_numpy(), b.to_numpy(), equal_nan=True), n


def _mean_reverting(n, seed=3):
    """A sideways market: price keeps getting pulled back to 2000."""
    rng = np.random.default_rng(seed)
    x = [2000.0]
    for _ in range(n - 1):
        x.append(x[-1] + 0.3 * (2000.0 - x[-1]) + rng.normal(0, 1.0))
    close = np.array(x)
    open_ = np.concatenate([[2000.0], close[:-1]])
    wick = np.abs(rng.normal(0, 0.5, n))
    return pd.DataFrame({"timestamp": pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC"), "open": open_,
                         "high": np.maximum(open_, close) + wick, "low": np.minimum(open_, close) - wick,
                         "close": close, "volume": 1.0})


def test_regimes_follow_the_market():
    up = label_regimes(compute_features(make_candles(900, drift=0.6, vol=1.0), CFG), CFG).iloc[300:]
    down = label_regimes(compute_features(make_candles(900, start=3000, drift=-0.6, vol=1.0), CFG), CFG).iloc[300:]
    flat = label_regimes(compute_features(_mean_reverting(900), CFG), CFG).iloc[300:]
    assert (up["regime"] == "TRENDING_UP").mean() > 0.6
    assert (down["regime"] == "TRENDING_DOWN").mean() > 0.6
    assert (flat["regime"].isin(["RANGING", "UNCERTAIN"])).mean() > 0.7
    assert (flat["regime"] == "RANGING").mean() > 0.4


def test_volatility_spike_is_labelled_high():
    df = make_candles(900, vol=0.5)
    spike = make_candles(60, start=df["close"].iloc[-1], vol=4.0, seed=9, t0=str(df["timestamp"].iloc[-1] + pd.Timedelta(minutes=15)))
    f = label_regimes(compute_features(pd.concat([df, spike], ignore_index=True), CFG), CFG)
    assert (f["vol_regime"].iloc[-40:] == "HIGH_VOLATILITY").mean() > 0.8
    assert (f["regime"].iloc[:199] == "WARMUP").all() and (f["regime"].iloc[200:] != "WARMUP").all()


def _row(**kw):
    base = dict(regime="TRENDING_DOWN", vol_regime="NORMAL_VOLATILITY", ema_fast=99.0, ema_slow=100.0, ema_trend=101.0,
                close=98.0, rsi=40.0, adx=25.0, bb_upper=103.0, bb_lower=97.0)
    base.update(kw)
    return pd.Series(base)


def test_trend_entry_rules():
    cfg = EntryCfg()
    assert evaluate_entry(_row(), cfg).direction == "SELL"
    assert evaluate_entry(_row(regime="TRENDING_UP", ema_fast=101.0, close=102.0, rsi=60.0), cfg).direction == "BUY"
    assert evaluate_entry(_row(rsi=55.0), cfg).direction == "NONE"               # RSI disagrees
    assert evaluate_entry(_row(close=99.5), cfg).direction == "NONE"             # price above EMA20
    assert evaluate_entry(_row(adx=15.0, regime="RANGING"), cfg).direction == "NONE"  # trend too weak


def test_unclear_or_wild_markets_are_skipped():
    cfg = EntryCfg()
    assert evaluate_entry(_row(regime="UNCERTAIN"), cfg).direction == "NONE"
    assert evaluate_entry(_row(regime="WARMUP"), cfg).direction == "NONE"
    assert evaluate_entry(_row(vol_regime="HIGH_VOLATILITY"), cfg).direction == "NONE"
    assert evaluate_entry(_row(vol_regime="HIGH_VOLATILITY"), EntryCfg(skip_high_volatility=False)).direction == "SELL"


def test_optional_ema200_alignment():
    cfg = EntryCfg(require_ema_trend_alignment=True)
    assert evaluate_entry(_row(close=98.0, ema_trend=97.0), cfg).direction == "NONE"
    assert evaluate_entry(_row(close=98.0, ema_trend=101.0), cfg).direction == "SELL"


def test_range_fade_rules():
    cfg = EntryCfg(mode=EntryMode.RANGE_FADE)
    assert evaluate_entry(_row(regime="RANGING", close=96.9, rsi=25.0), cfg).direction == "BUY"
    assert evaluate_entry(_row(regime="RANGING", close=103.5, rsi=75.0), cfg).direction == "SELL"
    assert evaluate_entry(_row(regime="TRENDING_DOWN", close=96.9, rsi=25.0), cfg).direction == "NONE"


def test_signal_confirmation():
    cfg = EntryCfg()
    assert still_supports(_row(), "SELL", cfg)
    assert not still_supports(_row(ema_fast=101.0), "SELL", cfg)
    assert not still_supports(_row(regime="TRENDING_UP"), "SELL", cfg)
    assert still_supports(_row(regime="TRENDING_UP", ema_fast=101.0), "BUY", cfg)
