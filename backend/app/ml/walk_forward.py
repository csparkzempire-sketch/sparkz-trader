"""
Walk-forward evaluation.

Markets are non-stationary, so a single chronological train/val/test split
(app.ml.dataset.build_dataset) isn't enough for real confidence. This module
slides a train/validate/test window forward across the full history and
records performance in each window, so you can see whether a strategy's
edge holds up across different periods rather than being an artifact of one
lucky split.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.metrics import compute_metrics
from app.config import Settings, settings
from app.features.feature_engineering import build_feature_matrix, get_feature_columns
from app.ml.dataset import add_labels
from app.ml.train import build_model
from app.strategy.signals import signal_from_probability


@dataclass
class WalkForwardWindowResult:
    window: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    total_return_pct: float
    max_drawdown_pct: float
    total_trades: int
    win_rate_pct: float
    profit_factor: float | None


def run_walk_forward(
    raw_df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    model_type: str = "random_forest",
    train_bars: int = 2000,
    test_bars: int = 500,
    step_bars: int | None = None,
    cfg: Settings | None = None,
) -> list[WalkForwardWindowResult]:
    """
    Slides a (train_bars -> test_bars) window forward by `step_bars`
    (default = test_bars, i.e. non-overlapping test windows) across the
    full dataset. In each window: fit the model on train_bars only, predict
    on test_bars, convert to signals, and backtest the test segment.
    """
    cfg = cfg or settings
    step_bars = step_bars or test_bars

    featured = build_feature_matrix(raw_df, cfg)
    labeled = add_labels(featured, cfg.lookahead_period, cfg.target_return_threshold, cfg)
    feature_columns = get_feature_columns(labeled)
    usable = labeled.dropna(subset=feature_columns + ["target", "atr"]).reset_index(drop=True)

    results: list[WalkForwardWindowResult] = []
    window_idx = 0
    start = 0
    while start + train_bars + test_bars <= len(usable):
        train_slice = usable.iloc[start:start + train_bars]
        test_slice = usable.iloc[start + train_bars:start + train_bars + test_bars].copy()

        model = build_model(model_type)
        model.fit(train_slice[feature_columns], train_slice["target"].astype(int))

        proba_up = model.predict_proba(test_slice[feature_columns])[:, 1]
        signals = [signal_from_probability(p, cfg=cfg).signal for p in proba_up]
        test_slice["signal"] = signals

        bt_config = BacktestConfig.from_settings(cfg, symbol, timeframe)
        engine = BacktestEngine(bt_config)
        result = engine.run(test_slice, signal_col="signal")
        metrics = compute_metrics(result.portfolio, timeframe)

        results.append(
            WalkForwardWindowResult(
                window=window_idx,
                train_start=str(train_slice["timestamp"].iloc[0]),
                train_end=str(train_slice["timestamp"].iloc[-1]),
                test_start=str(test_slice["timestamp"].iloc[0]),
                test_end=str(test_slice["timestamp"].iloc[-1]),
                total_return_pct=metrics.total_return_pct,
                max_drawdown_pct=metrics.max_drawdown_pct,
                total_trades=metrics.total_trades,
                win_rate_pct=metrics.win_rate_pct,
                profit_factor=metrics.profit_factor if isinstance(metrics.profit_factor, float) else None,
            )
        )

        window_idx += 1
        start += step_bars

    return results
