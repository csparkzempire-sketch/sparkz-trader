from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import ModelSummary, PredictRequest, PredictResponse, TrainModelRequest, TrainModelResponse
from app.data.downloader import DownloadError, download_ohlcv
from app.data.validator import DataValidationError, validate_and_clean
from app.database.database import get_session_dep
from app.database.models import Model as ModelORM
from app.features.feature_engineering import build_feature_matrix
from app.ml.dataset import LeakageError, build_dataset
from app.ml.evaluate import evaluate_classification, feature_importance, sweep_signal_thresholds
from app.ml.model_registry import load_model_artifact
from app.ml.predict import ModelNotAvailableError, predict_latest
from app.ml.train import SUPPORTED_MODEL_TYPES, train_model
from app.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.post("/train", response_model=TrainModelResponse)
def train(req: TrainModelRequest, session: Session = Depends(get_session_dep)) -> TrainModelResponse:
    if req.model_type not in SUPPORTED_MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"model_type must be one of {SUPPORTED_MODEL_TYPES}")

    try:
        raw = download_ohlcv(symbol=req.symbol, timeframe=req.timeframe)
        clean, _report = validate_and_clean(raw, timeframe=req.timeframe)
    except (DownloadError, DataValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        dataset = build_dataset(clean)
    except LeakageError as exc:
        # This should never happen given the code path, but fail loudly per spec if it does.
        raise HTTPException(status_code=500, detail=f"Data leakage detected, aborting training: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = train_model(dataset, req.model_type, req.symbol, req.timeframe, req.hyperparameters)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    val_metrics = evaluate_classification(result.model, dataset.X_val, dataset.y_val)
    test_metrics = evaluate_classification(result.model, dataset.X_test, dataset.y_test)
    importances = feature_importance(result.model, dataset.feature_columns)
    sweep = sweep_signal_thresholds(result.model, dataset.X_val, dataset.y_val)

    result.metadata.metrics = {
        "validation": val_metrics.as_dict(),
        "test": test_metrics.as_dict(),
    }

    orm = ModelORM(
        model_id=result.model_id,
        model_type=req.model_type,
        symbol=req.symbol,
        timeframe=req.timeframe,
        training_start=result.metadata.training_start,
        training_end=result.metadata.training_end,
        validation_start=result.metadata.validation_start,
        validation_end=result.metadata.validation_end,
        test_start=result.metadata.test_start,
        test_end=result.metadata.test_end,
        features=result.metadata.features,
        hyperparameters=result.metadata.hyperparameters,
        metrics=result.metadata.metrics,
        artifact_path=result.metadata.artifact_path,
    )
    session.add(orm)
    session.commit()

    return TrainModelResponse(
        model_id=result.model_id,
        model_type=req.model_type,
        train_period=[str(dataset.train_period[0]), str(dataset.train_period[1])],
        validation_period=[str(dataset.val_period[0]), str(dataset.val_period[1])],
        test_period=[str(dataset.test_period[0]), str(dataset.test_period[1])],
        n_features=len(dataset.feature_columns),
        classification_metrics_validation=val_metrics.as_dict(),
        classification_metrics_test=test_metrics.as_dict(),
        feature_importance=importances,
        threshold_sweep=sweep,
    )


@router.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse:
    try:
        raw = download_ohlcv(symbol=req.symbol, timeframe=req.timeframe)
        clean, _report = validate_and_clean(raw, timeframe=req.timeframe)
    except (DownloadError, DataValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from app.features.feature_engineering import get_feature_columns

    featured = build_feature_matrix(clean)

    try:
        model = load_model_artifact(req.model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    feature_cols = get_feature_columns(featured)
    latest = featured.dropna(subset=feature_cols).tail(1)
    if latest.empty:
        raise HTTPException(status_code=400, detail="Not enough warmed-up data to generate a prediction.")

    try:
        result = predict_latest(req.model_id, latest[feature_cols])
    except ModelNotAvailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PredictResponse(
        model_id=result.model_id,
        symbol=req.symbol,
        timestamp=latest["timestamp"].iloc[0],
        probability_up=result.probability_up,
        probability_down=result.probability_down,
        signal=result.signal,
        explanation=result.explanation,
    )


@router.get("", response_model=list[ModelSummary])
def list_models(session: Session = Depends(get_session_dep)) -> list[ModelSummary]:
    rows = session.query(ModelORM).order_by(ModelORM.created_at.desc()).all()
    return [
        ModelSummary(
            model_id=r.model_id,
            model_type=r.model_type,
            symbol=r.symbol,
            timeframe=r.timeframe,
            created_at=r.created_at,
            metrics=r.metrics,
        )
        for r in rows
    ]


@router.get("/{model_id}", response_model=ModelSummary)
def get_model(model_id: str, session: Session = Depends(get_session_dep)) -> ModelSummary:
    r = session.query(ModelORM).filter(ModelORM.model_id == model_id).first()
    if r is None:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    return ModelSummary(
        model_id=r.model_id, model_type=r.model_type, symbol=r.symbol, timeframe=r.timeframe,
        created_at=r.created_at, metrics=r.metrics,
    )
