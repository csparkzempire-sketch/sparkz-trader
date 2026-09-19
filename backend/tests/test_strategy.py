from __future__ import annotations

import pandas as pd

from app.strategy.rules import baseline_signal
from app.strategy.signals import signal_from_probability


def test_baseline_signal_buy_condition():
    df = pd.DataFrame(
        {
            "ema_20": [1.10],
            "ema_50": [1.09],
            "rsi": [60.0],
            "close": [1.101],
        }
    )
    result = baseline_signal(df, ema_fast=20, ema_slow=50)
    assert result.iloc[0] == "BUY"


def test_baseline_signal_sell_condition():
    df = pd.DataFrame(
        {
            "ema_20": [1.09],
            "ema_50": [1.10],
            "rsi": [40.0],
            "close": [1.089],
        }
    )
    result = baseline_signal(df, ema_fast=20, ema_slow=50)
    assert result.iloc[0] == "SELL"


def test_baseline_signal_hold_when_mixed():
    df = pd.DataFrame(
        {
            "ema_20": [1.10],
            "ema_50": [1.09],
            "rsi": [40.0],  # RSI doesn't confirm the uptrend
            "close": [1.101],
        }
    )
    result = baseline_signal(df, ema_fast=20, ema_slow=50)
    assert result.iloc[0] == "HOLD"


def test_baseline_signal_hold_during_warmup():
    df = pd.DataFrame({"ema_20": [float("nan")], "ema_50": [1.09], "rsi": [60.0], "close": [1.10]})
    result = baseline_signal(df, ema_fast=20, ema_slow=50)
    assert result.iloc[0] == "HOLD"


def test_signal_from_probability_buy():
    result = signal_from_probability(0.65, buy_threshold=0.60, sell_threshold=0.60)
    assert result.signal == "BUY"
    assert result.probability_down == 0.35


def test_signal_from_probability_sell():
    result = signal_from_probability(0.30, buy_threshold=0.60, sell_threshold=0.60)
    assert result.signal == "SELL"


def test_signal_from_probability_hold_in_uncertain_zone():
    result = signal_from_probability(0.55, buy_threshold=0.60, sell_threshold=0.60)
    assert result.signal == "HOLD"


def test_signal_from_probability_never_claims_certainty():
    result = signal_from_probability(0.99, buy_threshold=0.60, sell_threshold=0.60)
    lowered = result.explanation.lower()
    for forbidden in ["guarantee", "certain", "risk-free", "100% accurate"]:
        assert forbidden not in lowered
