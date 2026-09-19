"""
Model training.

Supports: logistic_regression, random_forest, and xgboost (if the xgboost
package is installed — otherwise it's reported as unavailable rather than
silently substituted). No deep learning in v1, per spec.

Training NEVER touches the test set — only X_train/y_train are fit here.
Validation is used by app.ml.evaluate for threshold selection and model
comparison, not for fitting parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.ml.dataset import DatasetSplit
from app.ml.model_registry import ModelMetadata, new_model_id, save_model_artifact
from app.utils.logging import get_logger, kv
from app.utils.time import utc_now

logger = get_logger(__name__)

SUPPORTED_MODEL_TYPES = ("logistic_regression", "random_forest", "xgboost")


def _xgboost_available() -> bool:
    try:
        import xgboost  # noqa: F401
        return True
    except ImportError:
        return False


def build_model(model_type: str, hyperparameters: dict[str, Any] | None = None):
    hyperparameters = hyperparameters or {}

    if model_type == "logistic_regression":
        return Pipeline(
            [
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(max_iter=1000, **hyperparameters)),
            ]
        )
    elif model_type == "random_forest":
        defaults = {"n_estimators": 300, "max_depth": 6, "min_samples_leaf": 20, "random_state": 42, "n_jobs": -1}
        defaults.update(hyperparameters)
        return RandomForestClassifier(**defaults)
    elif model_type == "xgboost":
        if not _xgboost_available():
            raise RuntimeError(
                "xgboost is not installed in this environment. Install it (`pip install xgboost`) "
                "or choose 'logistic_regression' / 'random_forest' instead."
            )
        from xgboost import XGBClassifier

        defaults = {"n_estimators": 300, "max_depth": 4, "learning_rate": 0.05, "random_state": 42, "eval_metric": "logloss"}
        defaults.update(hyperparameters)
        return XGBClassifier(**defaults)
    else:
        raise ValueError(f"Unknown model_type {model_type!r}. Supported: {SUPPORTED_MODEL_TYPES}")


@dataclass
class TrainResult:
    model: Any
    model_id: str
    metadata: ModelMetadata


def train_model(
    dataset: DatasetSplit,
    model_type: str,
    symbol: str,
    timeframe: str,
    hyperparameters: dict[str, Any] | None = None,
) -> TrainResult:
    hyperparameters = hyperparameters or {}
    model = build_model(model_type, hyperparameters)

    logger.info("Training model %s", kv(model_type=model_type, symbol=symbol, n_train=len(dataset.X_train)))
    model.fit(dataset.X_train, dataset.y_train)

    model_id = new_model_id(model_type, symbol)
    artifact_path = save_model_artifact(model, model_id)

    metadata = ModelMetadata(
        model_id=model_id,
        model_type=model_type,
        symbol=symbol,
        timeframe=timeframe,
        training_start=dataset.train_period[0].to_pydatetime(),
        training_end=dataset.train_period[1].to_pydatetime(),
        validation_start=dataset.val_period[0].to_pydatetime(),
        validation_end=dataset.val_period[1].to_pydatetime(),
        test_start=dataset.test_period[0].to_pydatetime(),
        test_end=dataset.test_period[1].to_pydatetime(),
        features=dataset.feature_columns,
        hyperparameters=hyperparameters,
        metrics={},  # filled in by app.ml.evaluate after evaluation
        artifact_path=str(artifact_path),
        created_at=utc_now(),
    )

    return TrainResult(model=model, model_id=model_id, metadata=metadata)
