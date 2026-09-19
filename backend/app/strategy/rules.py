"""
Deterministic baseline strategy rules (v1, before any ML).

BUY:  EMA_fast > EMA_slow AND RSI > rsi_buy_threshold AND close > EMA_fast
SELL: EMA_fast < EMA_slow AND RSI < rsi_sell_threshold AND close < EMA_fast
HOLD: otherwise

Thresholds are configurable (see app.config.Settings) and are NOT
auto-optimized in v1 — this is a fixed baseline to compare everything else
against.
"""

from __future__ import annotations

import pandas as pd

from app.config import Settings, settings


def baseline_signal(
    df: pd.DataFrame,
    ema_fast: int | None = None,
    ema_slow: int | None = None,
    rsi_buy_threshold: float | None = None,
    rsi_sell_threshold: float | None = None,
    cfg: Settings | None = None,
) -> pd.Series:
    """
    Vectorized baseline signal. Requires columns: ema_<fast>, ema_<slow>, rsi, close
    (i.e. run app.features.feature_engineering.build_feature_matrix first).

    Returns a Series of {"BUY", "SELL", "HOLD"}.
    """
    cfg = cfg or settings
    ema_fast = ema_fast or cfg.ema_fast
    ema_slow = ema_slow or cfg.ema_slow
    rsi_buy_threshold = rsi_buy_threshold if rsi_buy_threshold is not None else cfg.rsi_buy_threshold
    rsi_sell_threshold = rsi_sell_threshold if rsi_sell_threshold is not None else cfg.rsi_sell_threshold

    fast_col = f"ema_{ema_fast}"
    slow_col = f"ema_{ema_slow}"
    required = {fast_col, slow_col, "rsi", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"baseline_signal requires columns {sorted(required)}; missing {sorted(missing)}")

    buy = (df[fast_col] > df[slow_col]) & (df["rsi"] > rsi_buy_threshold) & (df["close"] > df[fast_col])
    sell = (df[fast_col] < df[slow_col]) & (df["rsi"] < rsi_sell_threshold) & (df["close"] < df[fast_col])

    signal = pd.Series("HOLD", index=df.index, name="signal")
    signal[buy] = "BUY"
    signal[sell] = "SELL"
    # Rows where indicators aren't warmed up yet (NaN) must be HOLD, never a
    # spurious signal from comparing NaNs.
    warmup_mask = df[[fast_col, slow_col, "rsi"]].isna().any(axis=1)
    signal[warmup_mask] = "HOLD"
    return signal
