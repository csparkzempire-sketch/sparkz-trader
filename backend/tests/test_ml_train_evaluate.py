from __future__ import annotations

from app.ml.dataset import build_dataset
from app.ml.evaluate import evaluate_classification, feature_importance, sweep_signal_thresholds
from app.ml.train import build_model, train_model


def test_train_logistic_regression_end_to_end(synthetic_ohlcv, tmp_path, monkeypatch):
    import app.ml.model_registry as registry

    monkeypatch.setattr(registry, "MODELS_DIR", tmp_path)

    dataset = build_dataset(synthetic_ohlcv)
    result = train_model(dataset, "logistic_regression", "TESTUSD", "1h")

    assert result.model_id
    assert (tmp_path / f"{result.model_id}.joblib").exists()

    val_metrics = evaluate_classification(result.model, dataset.X_val, dataset.y_val)
    test_metrics = evaluate_classification(result.model, dataset.X_test, dataset.y_test)
    assert 0.0 <= val_metrics.accuracy <= 1.0
    assert 0.0 <= test_metrics.accuracy <= 1.0
    assert val_metrics.n_samples == len(dataset.y_val)
    assert test_metrics.n_samples == len(dataset.y_test)


def test_train_random_forest_end_to_end_with_feature_importance(synthetic_ohlcv, tmp_path, monkeypatch):
    import app.ml.model_registry as registry

    monkeypatch.setattr(registry, "MODELS_DIR", tmp_path)

    dataset = build_dataset(synthetic_ohlcv)
    result = train_model(dataset, "random_forest", "TESTUSD", "1h")

    importances = feature_importance(result.model, dataset.feature_columns)
    assert len(importances) == len(dataset.feature_columns)
    # importances should be sorted descending
    values = [i["importance"] for i in importances]
    assert values == sorted(values, reverse=True)


def test_threshold_sweep_uses_validation_not_test(synthetic_ohlcv, tmp_path, monkeypatch):
    import app.ml.model_registry as registry

    monkeypatch.setattr(registry, "MODELS_DIR", tmp_path)

    dataset = build_dataset(synthetic_ohlcv)
    result = train_model(dataset, "random_forest", "TESTUSD", "1h")

    sweep = sweep_signal_thresholds(result.model, dataset.X_val, dataset.y_val)
    assert len(sweep) > 0
    for row in sweep:
        assert "threshold" in row and "n_signals" in row


def test_threshold_sweep_default_range_includes_below_half(synthetic_ohlcv, tmp_path, monkeypatch):
    """
    Regression test: the default sweep used to start at 0.50, which is
    useless when the positive class is rare and the model never predicts
    a probability >= 0.5 for anything (evaluate_classification's precision/
    recall, which use sklearn's fixed 0.5 cutoff via model.predict(), can
    show 0.0/0.0 in exactly that case even when the model's probabilities
    rank cases correctly -- the fix is being able to see thresholds below
    0.5 too, not just above it.
    """
    import app.ml.model_registry as registry

    monkeypatch.setattr(registry, "MODELS_DIR", tmp_path)

    dataset = build_dataset(synthetic_ohlcv)
    result = train_model(dataset, "random_forest", "TESTUSD", "1h")

    sweep = sweep_signal_thresholds(result.model, dataset.X_val, dataset.y_val)
    thresholds = [row["threshold"] for row in sweep]
    assert min(thresholds) < 0.5
    assert max(thresholds) > 0.5


def test_model_never_trained_on_test_set(synthetic_ohlcv, tmp_path, monkeypatch):
    """
    Sanity check: refit an identical model manually only on X_train, and
    confirm its predictions on X_test match the registry-trained model's
    predictions exactly (proves the test set never touched .fit()).
    """
    import app.ml.model_registry as registry

    monkeypatch.setattr(registry, "MODELS_DIR", tmp_path)

    dataset = build_dataset(synthetic_ohlcv)
    result = train_model(dataset, "logistic_regression", "TESTUSD", "1h")

    reference_model = build_model("logistic_regression")
    reference_model.fit(dataset.X_train, dataset.y_train)

    import numpy as np

    preds_a = result.model.predict_proba(dataset.X_test)
    preds_b = reference_model.predict_proba(dataset.X_test)
    np.testing.assert_allclose(preds_a, preds_b)
