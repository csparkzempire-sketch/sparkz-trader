"""Momentum indicators: RSI, MACD. All causal (no forward-looking windows)."""

from __future__ import annotations

import pandas as pd

from app.indicators.trend import ema


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing (as an EWM)."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    result = 100 - (100 / (1 + rs))
    # Where avg_loss is 0 and avg_gain > 0, RSI is 100 (all gains, no losses).
    result = result.where(avg_loss != 0, 100.0)
    return result.astype(float)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Returns a DataFrame with columns: macd, macd_signal, macd_hist."""
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "macd_signal": signal_line, "macd_hist": hist})


def add_momentum_indicators(df: pd.DataFrame, rsi_period: int = 14) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = rsi(df["close"], rsi_period)
    macd_df = macd(df["close"])
    df["macd"] = macd_df["macd"]
    df["macd_signal"] = macd_df["macd_signal"]
    df["macd_hist"] = macd_df["macd_hist"]
    return df
