"""
Initial-entry engine: decides whether a new basket may start, and in which
direction, from the analysis of the bar that just closed.

TREND mode (the spec's baseline rules):
  BUY  when EMA20 > EMA50, close > EMA20, RSI > rsi_buy_min, ADX >= min_trend_strength
  SELL when EMA20 < EMA50, close < EMA20, RSI < rsi_sell_max, ADX >= min_trend_strength
  optional: close on the matching side of EMA200
RANGE_FADE mode:
  in a RANGING regime only, BUY at/below the lower Bollinger band with RSI <= fade_rsi_low,
  SELL at/above the upper band with RSI >= fade_rsi_high

Always: no trade in WARMUP or UNCERTAIN regimes, and (by default) none in
HIGH_VOLATILITY. When the rules don't all agree the answer is NONE: the
engine never forces a trade.

`still_supports` answers the signal-confirmed grid's question: does the
analysis still back the basket's direction?
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.config import EntryCfg, EntryMode


@dataclass
class EntryDecision:
    direction: str            # "BUY", "SELL" or "NONE"
    reasons: list[str] = field(default_factory=list)


def evaluate_entry(row: pd.Series, cfg: EntryCfg) -> EntryDecision:
    regime, vol = row["regime"], row["vol_regime"]
    if regime in ("WARMUP", "UNCERTAIN"):
        return EntryDecision("NONE", [f"regime {regime}: no trade"])
    if cfg.skip_high_volatility and vol == "HIGH_VOLATILITY":
        return EntryDecision("NONE", ["high volatility: no new basket"])

    if cfg.mode == EntryMode.TREND:
        buy = [row["ema_fast"] > row["ema_slow"], row["close"] > row["ema_fast"], row["rsi"] > cfg.rsi_buy_min,
               row["adx"] >= cfg.min_trend_strength]
        sell = [row["ema_fast"] < row["ema_slow"], row["close"] < row["ema_fast"], row["rsi"] < cfg.rsi_sell_max,
                row["adx"] >= cfg.min_trend_strength]
        if cfg.require_ema_trend_alignment:
            buy.append(row["close"] > row["ema_trend"])
            sell.append(row["close"] < row["ema_trend"])
        if all(buy):
            return EntryDecision("BUY", [f"EMA20>EMA50, close>EMA20, RSI {row['rsi']:.1f}, ADX {row['adx']:.1f}"])
        if all(sell):
            return EntryDecision("SELL", [f"EMA20<EMA50, close<EMA20, RSI {row['rsi']:.1f}, ADX {row['adx']:.1f}"])
        return EntryDecision("NONE", ["trend rules not all met"])

    if regime != "RANGING":
        return EntryDecision("NONE", [f"range fade needs RANGING, regime is {regime}"])
    if row["close"] <= row["bb_lower"] and row["rsi"] <= cfg.fade_rsi_low:
        return EntryDecision("BUY", [f"lower band touch, RSI {row['rsi']:.1f}"])
    if row["close"] >= row["bb_upper"] and row["rsi"] >= cfg.fade_rsi_high:
        return EntryDecision("SELL", [f"upper band touch, RSI {row['rsi']:.1f}"])
    return EntryDecision("NONE", ["no band extreme"])


def still_supports(row: pd.Series, direction: str, cfg: EntryCfg) -> bool:
    """Signal-confirmed grid: add only while the analysis still backs the basket's direction."""
    if row["regime"] in ("WARMUP",):
        return False
    if cfg.mode == EntryMode.TREND:
        if direction == "SELL":
            return row["ema_fast"] < row["ema_slow"] and row["regime"] != "TRENDING_UP"
        return row["ema_fast"] > row["ema_slow"] and row["regime"] != "TRENDING_DOWN"
    return row["regime"] == "RANGING"
