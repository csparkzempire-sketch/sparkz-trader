"""
Market regime detection.

Regimes: TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOLATILITY, LOW_VOLATILITY.

These are measurable, rule-based classifications — not subjective claims
about market conditions. Volatility regimes take priority over trend
regimes when volatility is extreme, since execution/risk assumptions break
down most under extreme volatility regardless of trend direction.
"""

from __future__ import annotations

import pandas as pd


def classify_regime(
    df: pd.DataFrame,
    ema_fast_col: str = "ema_20",
    ema_slow_col: str = "ema_50",
    vol_col: str = "rolling_volatility",
    high_vol_quantile: float = 0.80,
    low_vol_quantile: float = 0.20,
    range_band_pct: float = 0.001,
) -> pd.Series:
    """
    Rule-based regime classifier.

    - HIGH_VOLATILITY: rolling_volatility above its (trailing-expanding)
      high_vol_quantile, computed using only data up to and including the
      current row (expanding quantile, not the full-series quantile, to
      avoid look-ahead).
    - LOW_VOLATILITY: below the low_vol_quantile, using the same logic.
    - TRENDING_UP / TRENDING_DOWN: EMA fast vs slow separated by more than
      range_band_pct of price.
    - RANGING: EMAs within range_band_pct of each other and volatility not
      extreme.
    """
    required = {ema_fast_col, ema_slow_col, vol_col, "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"classify_regime requires columns {sorted(required)}; missing {sorted(missing)}")

    # Expanding quantiles: at row i, use only rows [0, i] — strictly causal.
    high_thresh = df[vol_col].expanding(min_periods=20).quantile(high_vol_quantile)
    low_thresh = df[vol_col].expanding(min_periods=20).quantile(low_vol_quantile)

    ema_gap_pct = (df[ema_fast_col] - df[ema_slow_col]).abs() / df["close"]

    regime = pd.Series("RANGING", index=df.index, name="regime")

    trending_up = (df[ema_fast_col] > df[ema_slow_col]) & (ema_gap_pct > range_band_pct)
    trending_down = (df[ema_fast_col] < df[ema_slow_col]) & (ema_gap_pct > range_band_pct)
    regime[trending_up] = "TRENDING_UP"
    regime[trending_down] = "TRENDING_DOWN"

    high_vol = df[vol_col] >= high_thresh
    low_vol = df[vol_col] <= low_thresh
    # Volatility regime overrides trend classification when extreme, since
    # execution/risk assumptions matter more than direction in that case.
    regime[high_vol] = "HIGH_VOLATILITY"
    regime[low_vol & ~high_vol] = "LOW_VOLATILITY"

    warmup_mask = df[[ema_fast_col, ema_slow_col, vol_col]].isna().any(axis=1)
    regime[warmup_mask] = pd.NA
    return regime
