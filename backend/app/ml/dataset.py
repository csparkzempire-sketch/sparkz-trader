"""
ML dataset construction.

This is the single most safety-critical file for data leakage. Rules
enforced here:

1. The label (`target`) is computed ONLY from future price data
   (future_close / current_close), and that future data is NEVER included
   in the feature matrix.
2. Splitting is strictly chronological — train, then validation, then test,
   in time order. We never shuffle before splitting.
3. `build_dataset` explicitly asserts that no forbidden column
   (app.features.feature_engineering.FORBIDDEN_FEATURE_COLUMNS) leaks into
   the feature matrix, and raises loudly (LeakageError) if one does.
4. The last `lookahead_period` rows cannot have a valid label (their future
   isn't known yet) and are dropped from the trainable set.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.config import Settings, settings
from app.features.feature_engineering import FORBIDDEN_FEATURE_COLUMNS, build_feature_matrix, get_feature_columns


class LeakageError(RuntimeError):
    """Raised when a forbidden or future-looking column is detected in the feature matrix."""


@dataclass
class DatasetSplit:
    X_train: pd.DataFrame
    y_train: pd.Series
    X_val: pd.DataFrame
    y_val: pd.Series
    X_test: pd.DataFrame
    y_test: pd.Series
    feature_columns: list[str]
    train_period: tuple[pd.Timestamp, pd.Timestamp]
    val_period: tuple[pd.Timestamp, pd.Timestamp]
    test_period: tuple[pd.Timestamp, pd.Timestamp]
    full_df: pd.DataFrame  # includes timestamp + target, for later inspection


def add_labels(
    df: pd.DataFrame,
    lookahead_period: int | None = None,
    target_return_threshold: float | None = None,
    cfg: Settings | None = None,
) -> pd.DataFrame:
    """
    Adds `future_close`, `future_return`, and `target` columns.

    target = 1 if future_return > target_return_threshold, else 0.

    The last `lookahead_period` rows will have NaN future_return (their
    future isn't in the dataset) and correspondingly NaN target — callers
    must drop these before training (build_dataset does this).
    """
    cfg = cfg or settings
    lookahead_period = lookahead_period or cfg.lookahead_period
    target_return_threshold = target_return_threshold if target_return_threshold is not None else cfg.target_return_threshold

    df = df.copy()
    df["future_close"] = df["close"].shift(-lookahead_period)
    df["future_return"] = df["future_close"] / df["close"] - 1.0
    df["target"] = (df["future_return"] > target_return_threshold).astype("Int64")
    df.loc[df["future_return"].isna(), "target"] = pd.NA
    return df


def _assert_no_leakage(feature_columns: list[str]) -> None:
    leaked = set(feature_columns) & FORBIDDEN_FEATURE_COLUMNS
    if leaked:
        raise LeakageError(f"Forbidden future-looking columns found in feature matrix: {sorted(leaked)}")


def build_dataset(
    raw_df: pd.DataFrame,
    cfg: Settings | None = None,
    timeframe: str | None = None,
    higher_timeframes: list[str] | None = None,
) -> DatasetSplit:
    """
    Full pipeline: raw OHLCV -> features -> labels -> drop warmup/unlabeled
    rows -> chronological train/val/test split.

    `higher_timeframes` (e.g. ["4h", "1d"]) adds leakage-safe multi-
    timeframe context features -- see app.features.feature_engineering.
    add_multi_timeframe_features. Requires `timeframe` (raw_df's own
    timeframe) to also be given.
    """
    cfg = cfg or settings

    featured = build_feature_matrix(raw_df, cfg, timeframe=timeframe, higher_timeframes=higher_timeframes)
    labeled = add_labels(featured, cfg.lookahead_period, cfg.target_return_threshold, cfg)

    feature_columns = get_feature_columns(labeled)
    _assert_no_leakage(feature_columns)

    # Drop rows where features aren't warmed up yet OR label isn't known yet
    # (this is where we lose the first `max(indicator periods)` rows and the
    # last `lookahead_period` rows).
    usable = labeled.dropna(subset=feature_columns + ["target"]).reset_index(drop=True)
    if usable.empty:
        raise ValueError(
            "No usable rows remain after dropping warmup/unlabeled rows. "
            "The input dataset is likely too short for the configured indicator periods and lookahead."
        )

    n = len(usable)
    train_end = int(n * cfg.train_fraction)
    val_end = train_end + int(n * cfg.validation_fraction)

    train_df = usable.iloc[:train_end]
    val_df = usable.iloc[train_end:val_end]
    test_df = usable.iloc[val_end:]

    for name, part in [("train", train_df), ("validation", val_df), ("test", test_df)]:
        if part.empty:
            raise ValueError(
                f"The {name} split is empty after chronological splitting — "
                "increase the amount of historical data or adjust train/validation fractions."
            )

    def _period(part: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
        return part["timestamp"].iloc[0], part["timestamp"].iloc[-1]

    return DatasetSplit(
        X_train=train_df[feature_columns],
        y_train=train_df["target"].astype(int),
        X_val=val_df[feature_columns],
        y_val=val_df["target"].astype(int),
        X_test=test_df[feature_columns],
        y_test=test_df["target"].astype(int),
        feature_columns=feature_columns,
        train_period=_period(train_df),
        val_period=_period(val_df),
        test_period=_period(test_df),
        full_df=usable,
    )
