"""
Model registry: versioning and persistence of trained models.

Every trained model gets a unique model_id and is saved to models/ as a
joblib artifact, with metadata (periods, features, hyperparameters,
metrics) recorded in the database. Models are NEVER silently overwritten —
each training run produces a new model_id.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib

from app.utils.logging import get_logger, kv

logger = get_logger(__name__)

MODELS_DIR = Path(__file__).resolve().parents[3] / "models"


@dataclass
class ModelMetadata:
    model_id: str
    model_type: str
    symbol: str
    timeframe: str
    training_start: datetime
    training_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime
    features: list[str]
    hyperparameters: dict[str, Any]
    metrics: dict[str, Any]
    artifact_path: str
    created_at: datetime


def new_model_id(model_type: str, symbol: str) -> str:
    safe_symbol = symbol.replace("/", "_").replace("=", "_")
    return f"{model_type}_{safe_symbol}_{uuid.uuid4().hex[:8]}"


def save_model_artifact(model: Any, model_id: str) -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    path = MODELS_DIR / f"{model_id}.joblib"
    if path.exists():
        # Should not happen given uuid suffixing, but fail loudly rather
        # than silently overwrite a model, per spec.
        raise FileExistsError(f"Refusing to overwrite existing model artifact: {path}")
    joblib.dump(model, path)
    logger.info("Saved model artifact %s", kv(model_id=model_id, path=str(path)))
    return path


def load_model_artifact(model_id: str) -> Any:
    path = MODELS_DIR / f"{model_id}.joblib"
    if not path.exists():
        raise FileNotFoundError(f"No model artifact found for model_id={model_id!r} at {path}")
    return joblib.load(path)
