"""
Model evaluation.

Two distinct kinds of evaluation, deliberately kept separate:

1. Classification metrics (accuracy, precision, recall, F1, ROC-AUC,
   calibration) — how good is the model at predicting the label?
2. Trading metrics — what happens if you convert predictions into signals
   and run them through the backtester?

IMPORTANT: a model with high classification accuracy is NOT automatically
a profitable trading strategy (class imbalance, cost-of-error asymmetry,
and transaction costs all matter). Both numbers are reported; neither
implies the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from app.strategy.signals import signal_from_probability


@dataclass
class ClassificationMetrics:
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float | None
    n_samples: int
    positive_rate: float  # fraction of samples where target == 1

    def as_dict(self) -> dict:
        return {
            "accuracy": self.accuracy,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "roc_auc": self.roc_auc,
            "n_samples": self.n_samples,
            "positive_rate": self.positive_rate,
        }


def evaluate_classification(model: Any, X: pd.DataFrame, y: pd.Series) -> ClassificationMetrics:
    y_pred = model.predict(X)
    proba = model.predict_proba(X)[:, 1] if hasattr(model, "predict_proba") else None

    roc_auc = None
    if proba is not None and len(set(y)) > 1:
        try:
            roc_auc = float(roc_auc_score(y, proba))
        except ValueError:
            roc_auc = None

    return ClassificationMetrics(
        accuracy=round(float(accuracy_score(y, y_pred)), 4),
        precision=round(float(precision_score(y, y_pred, zero_division=0)), 4),
        recall=round(float(recall_score(y, y_pred, zero_division=0)), 4),
        f1=round(float(f1_score(y, y_pred, zero_division=0)), 4),
        roc_auc=round(roc_auc, 4) if roc_auc is not None else None,
        n_samples=len(y),
        positive_rate=round(float(y.mean()), 4),
    )


def calibration_curve_data(model: Any, X: pd.DataFrame, y: pd.Series, n_bins: int = 10) -> list[dict]:
    """Simple calibration check: for predictions bucketed by probability,
    compare mean predicted probability to observed positive rate."""
    if not hasattr(model, "predict_proba"):
        return []
    proba = model.predict_proba(X)[:, 1]
    df = pd.DataFrame({"proba": proba, "y": y.values})
    df["bucket"] = pd.qcut(df["proba"], q=min(n_bins, df["proba"].nunique()), duplicates="drop")
    grouped = df.groupby("bucket", observed=True).agg(mean_predicted=("proba", "mean"), observed_rate=("y", "mean"), n=("y", "size"))
    return [
        {"mean_predicted": round(float(r.mean_predicted), 4), "observed_rate": round(float(r.observed_rate), 4), "n": int(r.n)}
        for _, r in grouped.iterrows()
    ]


def feature_importance(model: Any, feature_columns: list[str]) -> list[dict]:
    """Feature importance for tree-based models. Sorting is for display
    only and does not imply causality."""
    importances = None
    if hasattr(model, "feature_importances_"):
        importances = model.feature_importances_
    elif hasattr(model, "named_steps") and "clf" in getattr(model, "named_steps", {}):
        clf = model.named_steps["clf"]
        if hasattr(clf, "coef_"):
            importances = np.abs(clf.coef_[0])
    if importances is None:
        return []
    pairs = sorted(zip(feature_columns, importances), key=lambda p: p[1], reverse=True)
    return [{"feature": f, "importance": round(float(v), 6)} for f, v in pairs]


def sweep_signal_thresholds(
    model: Any, X_val: pd.DataFrame, y_val: pd.Series, thresholds: list[float] | None = None
) -> list[dict]:
    """
    Evaluate different BUY probability thresholds on the validation set only
    (never the test set) to help choose signal_buy_threshold. Reports
    classification-style precision at each threshold, not backtested PnL —
    for full trading-metric evaluation, run the backtester on validation-
    period signals via app.backtest.engine.
    """
    if not hasattr(model, "predict_proba"):
        return []
    thresholds = thresholds or [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
    proba = model.predict_proba(X_val)[:, 1]
    results = []
    for t in thresholds:
        predicted_positive = proba >= t
        n_signals = int(predicted_positive.sum())
        if n_signals == 0:
            results.append({"threshold": t, "n_signals": 0, "precision": None})
            continue
        precision = float(y_val.values[predicted_positive].mean())
        results.append({"threshold": t, "n_signals": n_signals, "precision": round(precision, 4)})
    return results
