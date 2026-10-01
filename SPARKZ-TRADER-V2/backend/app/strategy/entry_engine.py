"""
Initial-entry engine: may a NEW basket start, and in which direction?

Outcomes:
  BUY / SELL   all conditions met
  WAIT         conditions partly there, or the market is unsuitable right now
               (warming up, uncertain regime, high volatility, score below threshold)
  NO_SIGNAL    the rules point nowhere

TREND mode (the spec's baseline):
  BUY  when EMA20 > EMA50, price > EMA20, RSI > 50   (plus filters)
  SELL when EMA20 < EMA50, price < EMA20, RSI < 50   (plus filters)
  filters: ADX >= min_trend_strength; no new basket in HIGH_VOLATILITY when
  volatility_filter is on; optionally price on the matching side of EMA200;
  the rule-agreement score must reach entry_threshold.
RANGE_FADE mode: in a RANGING market only, BUY at/below the lower Bollinger band
  with RSI <= fade_rsi_low, SELL at/above the upper band with RSI >= fade_rsi_high.

The engine never forces a trade: anything short of clear agreement is WAIT or NO_SIGNAL.

`thesis_holds` is the signal-confirmed grid's question: does the analysis still
back the basket's original direction? Once it says no, the grid stops adding.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import EntryCfg, EntryMode
from app.market.market_engine import MarketState
from app.strategy.analysis import Analysis


@dataclass
class EntryDecision:
    action: str                      # BUY, SELL, WAIT, NO_SIGNAL
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def is_entry(self) -> bool:
        return self.action in ("BUY", "SELL")


class EntryEngine:
    def __init__(self, cfg: EntryCfg):
        self.cfg = cfg

    def decide(self, s: MarketState, a: Analysis) -> EntryDecision:
        c = self.cfg
        if not s.ready:
            return EntryDecision("WAIT", ["indicators warming up"])
        if c.volatility_filter and s.vol_regime == "HIGH_VOLATILITY":
            return EntryDecision("WAIT", ["volatility filter: high volatility, no new basket"], a.confidence)
        if c.mode == EntryMode.RANGE_FADE:
            return self._fade(s)
        if s.trend_regime == "UNCERTAIN":
            return EntryDecision("WAIT", ["trend regime uncertain"], a.confidence)
        if a.direction == "NONE":
            return EntryDecision("NO_SIGNAL", a.reasons, a.confidence)
        if s.adx < c.min_trend_strength:
            return EntryDecision("WAIT", [f"trend too weak: ADX {s.adx:.1f} < {c.min_trend_strength:g}"], a.confidence)
        if c.require_ema200_alignment and (s.close <= s.ema200 if a.direction == "BUY" else s.close >= s.ema200):
            return EntryDecision("WAIT", ["price on the wrong side of EMA200"], a.confidence)
        if a.confidence < c.entry_threshold:
            return EntryDecision("WAIT", [f"rule agreement {a.confidence:.2f} below threshold {c.entry_threshold:.2f}"],
                                 a.confidence)
        return EntryDecision(a.direction, a.reasons, a.confidence)

    def _fade(self, s: MarketState) -> EntryDecision:
        c = self.cfg
        if s.trend_regime != "RANGING":
            return EntryDecision("WAIT", [f"range fade needs RANGING, regime is {s.trend_regime}"])
        if s.close <= s.bb_lower and s.rsi <= c.fade_rsi_low:
            return EntryDecision("BUY", [f"lower Bollinger band touch, RSI {s.rsi:.1f}"], 1.0)
        if s.close >= s.bb_upper and s.rsi >= c.fade_rsi_high:
            return EntryDecision("SELL", [f"upper Bollinger band touch, RSI {s.rsi:.1f}"], 1.0)
        return EntryDecision("NO_SIGNAL", ["no band extreme"])

    def thesis_holds(self, s: MarketState, direction: str) -> tuple[bool, str]:
        if not s.ready:
            return False, "indicators not ready"
        if self.cfg.mode == EntryMode.RANGE_FADE:
            return (s.trend_regime == "RANGING", f"regime {s.trend_regime}")
        # Breaks as soon as a bar CLOSES on the wrong side of EMA50 (well before EMA20 crosses EMA50,
        # which in tests came too late to matter: the grid was full by then).
        if direction == "SELL":
            if s.close >= s.ema50:
                return False, "bearish thesis broken: close back above EMA50"
            ok = s.ema20 < s.ema50 and s.trend_regime != "TRENDING_UP"
            return ok, "bearish trend intact" if ok else "bearish thesis broken (EMA20 >= EMA50 or uptrend)"
        if s.close <= s.ema50:
            return False, "bullish thesis broken: close back below EMA50"
        ok = s.ema20 > s.ema50 and s.trend_regime != "TRENDING_DOWN"
        return ok, "bullish trend intact" if ok else "bullish thesis broken (EMA20 <= EMA50 or downtrend)"
