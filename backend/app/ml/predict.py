"""
Model prediction.

Returns probability_up, probability_down, signal, model_version — never a
bare directional claim. If no model is trained/available, this module
raises rather than fabricating a prediction (spec section 28).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.config import Settings, settings
from app.ml.model_registry import load_model_artifact
from app.strategy.signals import SignalResult, signal_from_probability


class ModelNotAvailableError(RuntimeError):
    pass


@dataclass
class PredictionResult:
    probability_up: float
    probability_down: float
    signal: str
    model_id: str
    explanation: str


def predict_latest(
    model_id: str,
    features_row: pd.DataFrame,
    cfg: Settings | None = None,
) -> PredictionResult:
    """
    features_row: a single-row DataFrame containing exactly the feature
    columns the model was trained on (order-agnostic — sklearn/xgboost
    models match by column when given a DataFrame with matching names,
    but we still validate presence explicitly upstream in the API layer).
    """
    cfg = cfg or settings
    try:
        model = load_model_artifact(model_id)
    except FileNotFoundError as exc:
        raise ModelNotAvailableError(str(exc)) from exc

    if not hasattr(model, "predict_proba"):
        raise ModelNotAvailableError(f"Model {model_id!r} does not support probability output.")

    proba = model.predict_proba(features_row)[0]
    # Binary classifier: class 1 = "future return > threshold" (up event).
    probability_up = float(proba[1]) if len(proba) > 1 else float(proba[0])

    sig_result: SignalResult = signal_from_probability(probability_up, cfg=cfg)

    return PredictionResult(
        probability_up=sig_result.probability_up,
        probability_down=sig_result.probability_down,
        signal=sig_result.signal,
        model_id=model_id,
        explanation=sig_result.explanation,
    )
