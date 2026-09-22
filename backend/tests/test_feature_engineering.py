"""Tests for app.features.feature_engineering.

The zero-volume tests here exist because of a real bug found against live
EUR/USD data from Yahoo Finance: yfinance reports volume as a constant 0
for FX pairs. volume_change() is a pct_change(), and 0/0 is NaN for every
single row when volume never leaves zero -- that NaN column then poisoned
build_dataset()'s leak-guarded dropna step, silently emptying the entire
training set. The synthetic_ohlcv fixture used everywhere else in this
suite has random nonzero volume, so this class of bug was invisible to the
existing tests. These tests exercise the zero-volume path directly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.features.feature_engineering import build_feature_matrix, get_feature_columns
from app.ml.dataset import build_dataset


@pytest.fixture
def zero_volume_ohlcv(synthetic_ohlcv: pd.DataFrame) -> pd.DataFrame:
    """The same synthetic price data as everywhere else, but with volume
    forced to a constant 0 -- exactly what yfinance returns for FX pairs."""
    df = synthetic_ohlcv.copy()
    df["volume"] = 0.0
    return df


def test_normal_volume_produces_volume_features(synthetic_ohlcv: pd.DataFrame):
    featured = build_feature_matrix(synthetic_ohlcv)
    assert "volume_change" in featured.columns
    assert "volume_avg" in featured.columns


def test_zero_volume_skips_volume_features_instead_of_producing_all_nan(zero_volume_ohlcv: pd.DataFrame):
    featured = build_feature_matrix(zero_volume_ohlcv)
    # The features must simply be absent, not present-but-entirely-NaN --
    # an all-NaN column is what caused the original bug.
    assert "volume_change" not in featured.columns
    assert "volume_avg" not in featured.columns


def test_zero_volume_dataset_is_still_buildable_and_nonempty(zero_volume_ohlcv: pd.DataFrame):
    """This is the actual regression test: build_dataset must not raise
    'No usable rows remain' just because a symbol has no real volume data."""
    dataset = build_dataset(zero_volume_ohlcv)
    assert len(dataset.X_train) > 0
    assert len(dataset.X_val) > 0
    assert len(dataset.X_test) > 0
    assert "volume_change" not in dataset.feature_columns
    assert "volume_avg" not in dataset.feature_columns


def test_zero_volume_yields_same_row_count_as_nonzero_volume(synthetic_ohlcv: pd.DataFrame, zero_volume_ohlcv: pd.DataFrame):
    """Dropping volume features shouldn't cost usable rows elsewhere --
    the two runs should warm up and drop rows identically apart from the
    volume columns themselves."""
    with_volume = build_feature_matrix(synthetic_ohlcv)
    without_volume = build_feature_matrix(zero_volume_ohlcv)
    assert len(with_volume) == len(without_volume)


def test_nan_volume_column_also_treated_as_no_volume_data(synthetic_ohlcv: pd.DataFrame):
    """Entirely-missing volume (all NaN, e.g. a data source that omits it
    altogether) should be handled the same way as all-zero."""
    df = synthetic_ohlcv.copy()
    df["volume"] = np.nan
    featured = build_feature_matrix(df)
    assert "volume_change" not in featured.columns
    assert "volume_avg" not in featured.columns


def test_sporadic_zero_volume_does_not_produce_inf(synthetic_ohlcv: pd.DataFrame):
    """
    Regression test for a real crash found live on BTC-USD: mostly-nonzero
    volume with an occasional exact 0 (a real, legitimate value -- an
    illiquid hour, a data gap) makes plain pct_change() produce +inf on
    the 0 -> nonzero transition, not NaN. Unlike NaN, inf sails straight
    through dropna()-based leak guards and crashes sklearn training with
    'Input X contains infinity'. This must never happen: any inf in
    volume_change must become NaN, exactly like any other missing value.
    """
    df = synthetic_ohlcv.copy()
    df.loc[df.index[10], "volume"] = 0.0  # a single zero amid otherwise-real nonzero volume
    featured = build_feature_matrix(df)
    assert "volume_change" in featured.columns  # NOT the all-zero skip path -- mostly real volume
    assert not np.isinf(featured["volume_change"]).any()
    # the bar right after the zero is exactly where a naive pct_change
    # would have produced +inf -- confirm it's NaN there instead, not 0
    # and not some fabricated finite number.
    assert pd.isna(featured.loc[11, "volume_change"])


def test_dataset_buildable_with_sporadic_zero_volume(synthetic_ohlcv: pd.DataFrame):
    """The actual end-to-end regression: build_dataset must not crash (or
    silently pass inf through to sklearn) when volume has occasional
    zeros amid otherwise-real data."""
    df = synthetic_ohlcv.copy()
    df.loc[df.index[10], "volume"] = 0.0
    dataset = build_dataset(df)
    assert len(dataset.X_train) > 0
    assert not np.isinf(dataset.X_train.to_numpy(dtype=float)).any()


# --- Multi-timeframe features -----------------------------------------
#
# This is the highest-risk part of the whole feature pipeline for
# look-ahead bias: a naive resample-and-join can easily let a base-
# timeframe row see a higher-timeframe candle's indicators before that
# candle has actually closed. These tests are deliberately strict.

from app.features.feature_engineering import add_multi_timeframe_features


def test_multi_timeframe_adds_suffixed_columns(synthetic_ohlcv: pd.DataFrame):
    out = add_multi_timeframe_features(synthetic_ohlcv, "1h", ["4h"])
    htf_cols = [c for c in out.columns if c.endswith("_4h")]
    assert htf_cols, "expected at least one _4h-suffixed column to be added"
    # base timeframe's own indicator columns must be untouched (no _4h on them)
    assert "ema_20" not in htf_cols


def test_multi_timeframe_requires_timeframe_when_requested(synthetic_ohlcv: pd.DataFrame):
    with pytest.raises(ValueError):
        build_feature_matrix(synthetic_ohlcv, higher_timeframes=["4h"])  # no `timeframe` given


def test_multi_timeframe_skips_htf_not_longer_than_base(synthetic_ohlcv: pd.DataFrame):
    """A 'higher timeframe' that isn't actually higher than the base
    (e.g. requesting 1h context on a 1h base, or a shorter one) must be
    skipped, not silently misused."""
    out = add_multi_timeframe_features(synthetic_ohlcv, "1h", ["1h"])
    assert not [c for c in out.columns if c.endswith("_1h") and c not in synthetic_ohlcv.columns]


def test_multi_timeframe_no_lookahead_via_truncation(synthetic_ohlcv: pd.DataFrame):
    """
    The definitive leakage test, mirroring test_indicators.py's pattern:
    if computing multi-timeframe features on the full series and then
    truncating gives the SAME values as computing on the truncated series
    directly, no future bar (including a still-forming higher-timeframe
    candle) could have influenced an earlier row. If there were leakage,
    truncating the input would change earlier rows' values too.
    """
    full = add_multi_timeframe_features(synthetic_ohlcv, "1h", ["4h"])
    cutoff = 1000
    truncated_input = synthetic_ohlcv.iloc[:cutoff].reset_index(drop=True)
    truncated = add_multi_timeframe_features(truncated_input, "1h", ["4h"])

    htf_cols = [c for c in full.columns if c.endswith("_4h")]
    assert htf_cols
    pd.testing.assert_frame_equal(
        full.iloc[:cutoff][htf_cols].reset_index(drop=True),
        truncated[htf_cols].reset_index(drop=True),
    )


def test_multi_timeframe_value_only_updates_after_htf_bar_closes():
    """
    Explicit, hand-checkable version of the leakage guarantee: every base
    row within a still-forming higher-timeframe candle must see the SAME
    htf feature value (the previous, fully-closed candle's), and that
    value must only change starting at the base row where the htf candle
    has just closed -- never earlier, no matter what happens later inside
    that candle.
    """
    n = 40  # 40 hourly bars = ten 4h candles
    ts = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    # Gentle, distinct-enough price path so ema/rsi/atr aren't degenerate.
    close = 100.0 + np.cumsum(np.sin(np.arange(n) / 3.0)) + np.arange(n) * 0.05
    df = pd.DataFrame({
        "timestamp": ts,
        "open": close, "high": close + 0.5, "low": close - 0.5, "close": close,
        "volume": 100.0,
    })
    # Small EMA periods so indicators warm up within just a few 4h candles.
    tiny_cfg = settings.model_copy(update={"ema_fast": 2, "ema_slow": 3, "ema_long": 4, "rsi_period": 3, "atr_period": 3})

    out = add_multi_timeframe_features(df, "1h", ["4h"], cfg=tiny_cfg)
    col = "ema_2_4h"
    assert col in out.columns

    # 4h candles are [0,4), [4,8), [8,12), ... in hour-index terms.
    # Take a candle well past warmup, e.g. hours [24,28): rows 24,25,26,27
    # are all still "inside" that candle before it closes at hour 28.
    in_progress = out.loc[24:27, col].reset_index(drop=True)
    assert in_progress.nunique() == 1, "value must not change while the htf candle is still forming"
    # By this point (6 candles of warmup for ema_2/3/4), the column should
    # actually be populated, not NaN -- confirms this isn't a vacuous check.
    assert pd.notna(in_progress.iloc[0])

