"""
Signal engine.

Converts either the deterministic baseline rules OR an ML model's
probability output into a trading signal. Signals are always treated as
probabilistic research outputs, never as certainty. The BUY/SELL/HOLD
threshold logic here is intentionally configurable — 60% is a default,
not a claim that 60% is optimal (see app.ml.evaluate for threshold sweeps).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, settings


@dataclass(frozen=True)
class SignalResult:
    signal: str  # "BUY" | "SELL" | "HOLD"
    probability_up: float
    probability_down: float
    explanation: str


def signal_from_probability(
    probability_up: float,
    buy_threshold: float | None = None,
    sell_threshold: float | None = None,
    cfg: Settings | None = None,
) -> SignalResult:
    """
    Convert a model's P(up) into a signal.

    probability_down is defined as 1 - probability_up (binary classification
    framing: the event is "future return exceeds threshold" vs. not).
    """
    cfg = cfg or settings
    buy_threshold = buy_threshold if buy_threshold is not None else cfg.signal_buy_threshold
    sell_threshold = sell_threshold if sell_threshold is not None else cfg.signal_sell_threshold

    if not (0.0 <= probability_up <= 1.0):
        raise ValueError(f"probability_up must be in [0, 1], got {probability_up}")

    probability_down = 1.0 - probability_up

    if probability_up >= buy_threshold:
        signal = "BUY"
        explanation = (
            f"Model estimates a {probability_up:.1%} probability of the predefined upward "
            f"target event under the current model and data assumptions "
            f"(threshold {buy_threshold:.0%})."
        )
    elif probability_down >= sell_threshold:
        signal = "SELL"
        explanation = (
            f"Model estimates a {probability_down:.1%} probability of the predefined downward "
            f"target event under the current model and data assumptions "
            f"(threshold {sell_threshold:.0%})."
        )
    else:
        signal = "HOLD"
        explanation = (
            f"Model probability ({probability_up:.1%} up) does not clear either threshold "
            f"(buy>={buy_threshold:.0%}, sell>={sell_threshold:.0%}); no research edge to act on."
        )

    return SignalResult(
        signal=signal,
        probability_up=probability_up,
        probability_down=probability_down,
        explanation=explanation,
    )
