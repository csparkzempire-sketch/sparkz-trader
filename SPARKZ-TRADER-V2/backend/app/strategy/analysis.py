"""
Market analysis engine: reads a closed-bar MarketState and says which way the
evidence points, how strongly, and why.

Direction comes from the three core trend rules (EMA20 vs EMA50, price vs EMA20,
RSI vs 50). `confidence` is the share of seven directional checks that agree
with that direction:

  EMA20 vs EMA50, close vs EMA20, RSI vs 50, ADX >= min trend strength,
  MACD histogram sign, close vs EMA200, +DI vs -DI

IMPORTANT: confidence is a RULE-AGREEMENT SCORE between 0 and 1, not a
probability that the trade will win. It has not been calibrated against
outcomes, and the dashboard labels it accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.config import AnalysisCfg, EntryCfg
from app.market.market_engine import MarketState

CONFIDENCE_NOTE = "rule-agreement score, not a calibrated probability"


@dataclass
class Analysis:
    direction: str               # BUY, SELL or NONE
    confidence: float            # 0..1 rule agreement
    regime: str
    trend_regime: str
    vol_regime: str
    reasons: list[str] = field(default_factory=list)
    timestamp: datetime | None = None
    core_rules_met: bool = False

    def to_dict(self) -> dict:
        return {"direction": self.direction, "confidence": round(self.confidence, 3),
                "confidence_note": CONFIDENCE_NOTE, "regime": self.regime, "trend_regime": self.trend_regime,
                "vol_regime": self.vol_regime, "reasons": self.reasons, "core_rules_met": self.core_rules_met,
                "timestamp": self.timestamp.isoformat() if self.timestamp else None}


class MarketAnalysisEngine:
    def __init__(self, entry: EntryCfg, analysis: AnalysisCfg | None = None):
        self.e = entry
        self.a = analysis or AnalysisCfg()

    def _checks(self, s: MarketState, side: str) -> list[tuple[str, bool]]:
        up = side == "BUY"
        return [
            ("EMA20 above EMA50" if up else "EMA20 below EMA50", s.ema20 > s.ema50 if up else s.ema20 < s.ema50),
            ("price above EMA20" if up else "price below EMA20", s.close > s.ema20 if up else s.close < s.ema20),
            (f"RSI {s.rsi:.1f} {'above' if up else 'below'} 50",
             s.rsi > self.e.rsi_buy_min if up else s.rsi < self.e.rsi_sell_max),
            (f"trend strength ADX {s.adx:.1f} >= {self.e.min_trend_strength:g}", s.adx >= self.e.min_trend_strength),
            ("MACD histogram " + ("positive" if up else "negative"), s.macd_hist > 0 if up else s.macd_hist < 0),
            ("price " + ("above" if up else "below") + " EMA200", s.close > s.ema200 if up else s.close < s.ema200),
            ("+DI above -DI" if up else "-DI above +DI", s.plus_di > s.minus_di if up else s.minus_di > s.plus_di),
        ]

    def analyze(self, s: MarketState) -> Analysis:
        base = dict(regime=s.regime, trend_regime=s.trend_regime, vol_regime=s.vol_regime, timestamp=s.timestamp)
        if not s.ready:
            return Analysis("NONE", 0.0, reasons=["indicators warming up"], **base)
        scores = {}
        for side in ("BUY", "SELL"):
            ch = self._checks(s, side)
            scores[side] = (sum(ok for _, ok in ch) / len(ch), all(ok for _, ok in ch[:3]), ch)
        side = max(scores, key=lambda k: (scores[k][1], scores[k][0]))
        conf, core, ch = scores[side]
        if not core:
            return Analysis("NONE", conf, reasons=["core trend rules not aligned"] +
                            [f"{n}: {'yes' if ok else 'no'}" for n, ok in ch[:3]], **base)
        return Analysis(side, conf, reasons=[n for n, ok in ch if ok], core_rules_met=True, **base)
