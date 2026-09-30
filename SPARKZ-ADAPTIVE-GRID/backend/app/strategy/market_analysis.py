"""
Market-analysis features.

Every value on row N is computed from bars 0..N only (EMAs, Wilder
smoothing, rolling windows that end at N). Nothing is centred or
back-filled, so a decision made at the close of bar N never sees bar N+1.
tests/test_phase2_analysis.py checks this by recomputing on truncated data.

Features:
- EMA 20 / 50 / 200 and the distance of price from each, in ATRs
- RSI 14 (Wilder)
- ATR 14 (Wilder) and ATR as % of price
- MACD 12/26/9 (line, signal, histogram)
- Bollinger Bands 20 / 2 (upper, lower, width, %B)
- ADX 14 with +DI / -DI: trend strength
- realized volatility (std of log returns) and its trailing percentile
- recent return over `return_lookback` bars
- candle structure: body share of the range, upper and lower wick shares
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import AnalysisCfg


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain, loss = wilder(d.clip(lower=0), n), wilder(-d.clip(upper=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up, down = df["high"].diff(), -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = wilder(true_range(df), n)
    plus_di = 100 * wilder(plus_dm, n) / tr
    minus_di = 100 * wilder(minus_dm, n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder(dx, n), plus_di, minus_di


def trailing_percentile(s: pd.Series, window: int, min_periods: int) -> pd.Series:
    """Share of the trailing window (ending at this bar, inclusive) at or below this bar's value."""
    return s.rolling(window, min_periods=min_periods).apply(lambda x: (x <= x[-1]).mean(), raw=True)


def compute_features(df: pd.DataFrame, cfg: AnalysisCfg | None = None) -> pd.DataFrame:
    cfg = cfg or AnalysisCfg()
    f = df.copy()
    c = f["close"]
    f["ema_fast"] = ema(c, cfg.ema_fast)
    f["ema_slow"] = ema(c, cfg.ema_slow)
    f["ema_trend"] = ema(c, cfg.ema_trend)
    f["rsi"] = rsi(c, cfg.rsi_period)
    f["atr"] = atr(f, cfg.atr_period)
    f["atr_pct"] = f["atr"] / c * 100
    macd_line = ema(c, cfg.macd_fast) - ema(c, cfg.macd_slow)
    f["macd"] = macd_line
    f["macd_signal"] = macd_line.ewm(span=cfg.macd_signal, adjust=False, min_periods=cfg.macd_signal).mean()
    f["macd_hist"] = f["macd"] - f["macd_signal"]
    mid = c.rolling(cfg.bb_period).mean()
    sd = c.rolling(cfg.bb_period).std(ddof=0)
    f["bb_mid"], f["bb_upper"], f["bb_lower"] = mid, mid + cfg.bb_std * sd, mid - cfg.bb_std * sd
    f["bb_width"] = (f["bb_upper"] - f["bb_lower"]) / mid
    f["bb_pctb"] = (c - f["bb_lower"]) / (f["bb_upper"] - f["bb_lower"])
    f["adx"], f["plus_di"], f["minus_di"] = adx(f, cfg.adx_period)
    logret = np.log(c).diff()
    f["realized_vol"] = logret.rolling(20).std()
    f["vol_percentile"] = trailing_percentile(f["atr_pct"], cfg.vol_lookback, min(100, cfg.vol_lookback))
    f["ret_recent"] = c / c.shift(cfg.return_lookback) - 1
    rng = (f["high"] - f["low"]).replace(0, np.nan)
    f["body_share"] = (c - f["open"]).abs() / rng
    f["upper_wick_share"] = (f["high"] - f[["open", "close"]].max(axis=1)) / rng
    f["lower_wick_share"] = (f[["open", "close"]].min(axis=1) - f["low"]) / rng
    for name in ("ema_fast", "ema_slow", "ema_trend"):
        f[f"dist_{name}_atr"] = (c - f[name]) / f["atr"]
    return f


FEATURE_COLUMNS = ["ema_fast", "ema_slow", "ema_trend", "rsi", "atr", "adx", "vol_percentile", "bb_mid"]


def ready(row) -> bool:
    """True once every feature the strategy relies on has a value (after the warm-up)."""
    return all(pd.notna(row[c]) for c in FEATURE_COLUMNS)
