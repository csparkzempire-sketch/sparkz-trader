"""
Market data validation and cleaning.

This module NEVER shuffles rows. It only sorts chronologically, de-duplicates,
flags/removes invalid candles, and reports on data quality. It must run
before any indicator or feature calculation touches the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.utils.logging import get_logger, kv
from app.utils.time import timeframe_to_pandas_freq

logger = get_logger(__name__)

REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


@dataclass
class ValidationReport:
    input_rows: int
    output_rows: int
    duplicates_removed: int
    invalid_ohlc_removed: int
    missing_volume_filled: int
    missing_candles_detected: int
    missing_candle_timestamps: list[str] = field(default_factory=list)
    was_sorted: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "input_rows": self.input_rows,
            "output_rows": self.output_rows,
            "duplicates_removed": self.duplicates_removed,
            "invalid_ohlc_removed": self.invalid_ohlc_removed,
            "missing_volume_filled": self.missing_volume_filled,
            "missing_candles_detected": self.missing_candles_detected,
            "missing_candle_timestamps": self.missing_candle_timestamps[:50],  # cap for sanity
            "was_sorted": self.was_sorted,
            "notes": self.notes,
        }


class DataValidationError(ValueError):
    """Raised when data fails a hard validation requirement (missing columns, empty, etc.)."""


def validate_and_clean(
    df: pd.DataFrame,
    timeframe: str | None = None,
    drop_invalid_ohlc: bool = True,
) -> tuple[pd.DataFrame, ValidationReport]:
    """
    Validate and clean raw OHLCV data.

    Steps (in order, matching the spec):
      1. normalize column names / check required columns exist
      2. sort chronologically
      3. remove duplicate timestamps
      4. detect invalid OHLC values (e.g. high < low, negative prices)
      5. handle missing volume (fill with 0)
      6. detect missing candles vs. the expected frequency (report only —
         we do NOT fabricate synthetic candles by default)

    Returns (cleaned_df, ValidationReport). Raises DataValidationError for
    unrecoverable problems (missing required columns, empty input).
    """
    if df is None or df.empty:
        raise DataValidationError("Input DataFrame is empty — nothing to validate.")

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise DataValidationError(f"Missing required columns: {missing_cols}")

    input_rows = len(df)
    df = df.copy()

    # Ensure timestamp is a proper datetime (UTC) — never trust string sorting.
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    n_bad_ts = int(df["timestamp"].isna().sum())
    if n_bad_ts:
        df = df.dropna(subset=["timestamp"])

    # 1. Sort chronologically. This is a hard requirement — models and
    # backtests assume strictly increasing time order.
    was_sorted = not df["timestamp"].is_monotonic_increasing
    df = df.sort_values("timestamp").reset_index(drop=True)

    # 2. Remove duplicate timestamps (keep first occurrence).
    dup_mask = df["timestamp"].duplicated(keep="first")
    duplicates_removed = int(dup_mask.sum())
    df = df.loc[~dup_mask].reset_index(drop=True)

    # 3. Detect invalid OHLC values.
    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    invalid_mask = (
        df[["open", "high", "low", "close"]].isna().any(axis=1)
        | (df["high"] < df["low"])
        | (df["open"] <= 0)
        | (df["high"] <= 0)
        | (df["low"] <= 0)
        | (df["close"] <= 0)
        | (df["high"] < df["open"])
        | (df["high"] < df["close"])
        | (df["low"] > df["open"])
        | (df["low"] > df["close"])
    )
    invalid_ohlc_removed = int(invalid_mask.sum())
    if drop_invalid_ohlc and invalid_ohlc_removed:
        df = df.loc[~invalid_mask].reset_index(drop=True)

    # 4. Missing volume -> fill with 0, count how many.
    missing_volume_filled = int(df["volume"].isna().sum())
    df["volume"] = df["volume"].fillna(0.0)
    df.loc[df["volume"] < 0, "volume"] = 0.0

    # 5. Detect missing candles relative to expected frequency (report-only).
    missing_candles_detected = 0
    missing_timestamps: list[str] = []
    if timeframe is not None and len(df) > 1:
        try:
            freq = timeframe_to_pandas_freq(timeframe)
            full_range = pd.date_range(df["timestamp"].iloc[0], df["timestamp"].iloc[-1], freq=freq)
            existing = set(df["timestamp"])
            missing = [ts for ts in full_range if ts not in existing]
            missing_candles_detected = len(missing)
            missing_timestamps = [ts.isoformat() for ts in missing]
        except ValueError:
            pass  # unsupported timeframe string for freq inference — skip gap detection

    notes = []
    if n_bad_ts:
        notes.append(f"Dropped {n_bad_ts} rows with unparseable timestamps.")
    if missing_candles_detected:
        notes.append(
            f"Detected {missing_candles_detected} missing candles vs. expected frequency "
            "(common for FX weekends/holidays — not necessarily an error)."
        )

    report = ValidationReport(
        input_rows=input_rows,
        output_rows=len(df),
        duplicates_removed=duplicates_removed,
        invalid_ohlc_removed=invalid_ohlc_removed,
        missing_volume_filled=missing_volume_filled,
        missing_candles_detected=missing_candles_detected,
        missing_candle_timestamps=missing_timestamps,
        was_sorted=was_sorted,
        notes=notes,
    )

    logger.info("Validation complete %s", kv(**{k: v for k, v in report.as_dict().items() if k != "missing_candle_timestamps"}))

    if df.empty:
        raise DataValidationError("All rows were removed during validation — check the source data.")

    return df, report
