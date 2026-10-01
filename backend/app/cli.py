"""
SPARKZ TRADER command-line interface.

Usage:
    python -m app.cli download-data [--symbol EURUSD=X] [--timeframe 1h]   (merges into the local cache)
    python -m app.cli backtest [--symbol EURUSD=X] [--timeframe 1h] [--strategy baseline] [--use-cached]
    python -m app.cli train-model [--symbol EURUSD=X] [--timeframe 1h] [--model-type random_forest]
        [--lookahead-period N] [--target-return-threshold R]
    python -m app.cli walk-forward [--symbol EURUSD=X] [--timeframe 1h] [--model-type random_forest]
        [--train-bars N] [--test-bars N] [--step-bars N] [--window-mode rolling|expanding] [--purge-bars N]
        [--lookahead-period N] [--target-return-threshold R]
    python -m app.cli evaluate-model --model-id <id>
    python -m app.cli paper-trade --account NAME [--symbol BTC-USD] [--timeframe 1d] [--strategy baseline_long_only]
    python -m app.cli system-status
"""

from __future__ import annotations

import argparse
import json
import sys

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.metrics import compare_to_buy_and_hold, compute_metrics
from app.backtest.report import generate_and_save_report
from app.broker.factory import AVAILABLE_BROKERS
from app.config import settings
from app.data.downloader import DownloadError, download_ohlcv
from app.data.repository import cache_exists, load_processed, merge_into_cache, processed_file_path
from app.data.validator import DataValidationError, validate_and_clean
from app.features.feature_engineering import build_feature_matrix
from app.ml.dataset import LeakageError, build_dataset
from app.ml.evaluate import evaluate_classification, feature_importance, sweep_signal_thresholds
from app.ml.model_registry import load_model_artifact
from app.ml.train import train_model
from app.ml.walk_forward import run_walk_forward
from app.strategy.rules import RULE_STRATEGIES, rule_signal
from app.utils.logging import get_logger

logger = get_logger("cli", settings.log_level)


def _load_market_data(symbol: str, timeframe: str, use_cached: bool = False):
    """
    Return cleaned OHLCV for (symbol, timeframe): either straight from the
    local cache written by `download-data` (use_cached=True -- no network,
    so experiments keep working when Yahoo is down or rate-limiting), or
    freshly downloaded and validated.

    Raises DownloadError / DataValidationError / FileNotFoundError; callers
    print those and exit.
    """
    if use_cached:
        df = load_processed(symbol, timeframe)  # FileNotFoundError says which file is missing
        print(
            f"Using cached data: {len(df)} bars, {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]} "
            f"({processed_file_path(symbol, timeframe)})"
        )
        return df

    try:
        raw = download_ohlcv(symbol=symbol, timeframe=timeframe)
    except DownloadError as exc:
        if cache_exists(symbol, timeframe):
            raise DownloadError(
                f"{exc} A cached copy exists at {processed_file_path(symbol, timeframe)} -- "
                "re-run with --use-cached to work offline from it."
            ) from exc
        raise
    clean, _ = validate_and_clean(raw, timeframe=timeframe, symbol=symbol)
    return clean


def cmd_download_data(args) -> None:
    try:
        raw = download_ohlcv(symbol=args.symbol, timeframe=args.timeframe)
        clean, report = validate_and_clean(raw, timeframe=args.timeframe, symbol=args.symbol)
    except (DownloadError, DataValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # Merge into the cache rather than overwrite it, so a short (or
    # partial) download can never replace a longer history already saved.
    merged, path, n_before = merge_into_cache(clean, args.symbol, args.timeframe)
    print(
        f"Downloaded {len(clean)} candles. Cache now holds {len(merged)} candles "
        f"({merged['timestamp'].iloc[0]} -> {merged['timestamp'].iloc[-1]}), was {n_before}: {path}"
    )
    print(json.dumps(report.as_dict(), indent=2, default=str))


def cmd_backtest(args) -> None:
    try:
        clean = _load_market_data(args.symbol, args.timeframe, getattr(args, "use_cached", False))
    except (DownloadError, DataValidationError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.strategy in RULE_STRATEGIES:
        featured = build_feature_matrix(clean)
        featured["signal"] = rule_signal(featured, args.strategy)
    else:
        higher_timeframes = (
            [t.strip() for t in args.multi_timeframe.split(",")] if getattr(args, "multi_timeframe", None) else None
        )
        featured = build_feature_matrix(clean, timeframe=args.timeframe, higher_timeframes=higher_timeframes)
        model = load_model_artifact(args.strategy)
        from app.features.feature_engineering import get_feature_columns
        from app.ml.dataset import add_labels
        from app.strategy.signals import signal_from_probability

        # Use the same lookahead/threshold/multi-timeframe setup the model
        # was actually trained with, if given -- for lookahead/threshold
        # this only affects which trailing rows get dropped as unlabeled;
        # for multi-timeframe, a mismatch here will make predict_proba
        # fail outright on a feature-shape mismatch, since the model
        # expects the exact columns it was trained on.
        cfg = settings
        overrides = {}
        if getattr(args, "lookahead_period", None) is not None:
            overrides["lookahead_period"] = args.lookahead_period
        if getattr(args, "target_return_threshold", None) is not None:
            overrides["target_return_threshold"] = args.target_return_threshold
        if overrides:
            cfg = settings.model_copy(update=overrides)

        # CRITICAL: without this, the backtest runs across the model's own
        # TRAINING data too, and a fitted model can perform far better than
        # its real skill on rows it has already seen -- that's not a
        # backtest result, it's leakage. Restrict to the same chronological
        # test split build_dataset used at training time, unless the caller
        # explicitly opts out (--full-history) knowing that invalidates the
        # numbers as a performance estimate.
        if not getattr(args, "full_history", False):
            from app.ml.dataset import build_dataset
            ds = build_dataset(clean, cfg=cfg, timeframe=args.timeframe, higher_timeframes=higher_timeframes)
            test_start = ds.test_period[0]
            n_before = len(featured)
            featured = featured[featured["timestamp"] >= test_start].reset_index(drop=True)
            print(
                f"Restricting backtest to the model's held-out TEST period only "
                f"({test_start} onward, {len(featured)} of {n_before} total bars) -- "
                "trading on the model's own training data would inflate results with "
                "memorization, not real skill. Pass --full-history to override (NOT a "
                "valid performance estimate if you do)."
            )
        else:
            print(
                "WARNING: --full-history includes bars the model was TRAINED on. Any "
                "profit shown here may reflect memorization of the training data rather "
                "than real predictive skill -- do not treat this as a performance estimate.",
                file=sys.stderr,
            )

        labeled = add_labels(featured, cfg.lookahead_period, cfg.target_return_threshold, cfg)
        feature_cols = get_feature_columns(labeled)
        mask = labeled[feature_cols].notna().all(axis=1)
        featured["signal"] = "HOLD"
        proba = model.predict_proba(labeled.loc[mask, feature_cols])[:, 1]
        buy_threshold = getattr(args, "buy_threshold", None)
        sell_threshold = getattr(args, "sell_threshold", None)
        featured.loc[mask, "signal"] = [
            signal_from_probability(p, buy_threshold=buy_threshold, sell_threshold=sell_threshold, cfg=cfg).signal
            for p in proba
        ]
        n_buy = int((featured["signal"] == "BUY").sum())
        n_sell = int((featured["signal"] == "SELL").sum())
        effective_buy = buy_threshold if buy_threshold is not None else cfg.signal_buy_threshold
        effective_sell = sell_threshold if sell_threshold is not None else cfg.signal_sell_threshold
        print(
            f"Model signals: {n_buy} BUY, {n_sell} SELL out of {mask.sum()} scorable bars "
            f"(buy_threshold={effective_buy}, sell_threshold={effective_sell})."
        )
        if n_buy == 0 and n_sell == 0:
            print(
                "WARNING: zero signals fired -- the model's probabilities never crossed these "
                "thresholds. Check `train-model`'s threshold sweep output to find a threshold "
                "the model's probabilities actually reach, then pass it via --buy-threshold.",
                file=sys.stderr,
            )

    bt_config = BacktestConfig.from_settings(settings, args.symbol, args.timeframe)
    engine = BacktestEngine(bt_config)
    result = engine.run(featured, signal_col="signal")
    metrics = compute_metrics(result.portfolio, args.timeframe, args.symbol)
    baseline_cmp = compare_to_buy_and_hold(featured, bt_config.initial_capital)

    print(json.dumps(metrics.as_dict(), indent=2))
    print(json.dumps(baseline_cmp, indent=2))
    if result.warnings:
        print("WARNINGS:", result.warnings)

    import uuid as _uuid
    backtest_id = f"bt_{_uuid.uuid4().hex[:10]}"
    report_path = generate_and_save_report(result, metrics, backtest_id, baseline_df=featured)
    print(f"Report saved to {report_path}")


def cmd_train_model(args) -> None:
    cfg = settings
    overrides = {}
    if args.lookahead_period is not None:
        overrides["lookahead_period"] = args.lookahead_period
    if args.target_return_threshold is not None:
        overrides["target_return_threshold"] = args.target_return_threshold
    if overrides:
        cfg = settings.model_copy(update=overrides)

    try:
        clean = _load_market_data(args.symbol, args.timeframe, getattr(args, "use_cached", False))
        higher_timeframes = (
            [t.strip() for t in args.multi_timeframe.split(",")] if getattr(args, "multi_timeframe", None) else None
        )
        dataset = build_dataset(clean, cfg=cfg, timeframe=args.timeframe, higher_timeframes=higher_timeframes)
    except (DownloadError, DataValidationError, LeakageError, ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    result = train_model(dataset, args.model_type, args.symbol, args.timeframe)
    val_metrics = evaluate_classification(result.model, dataset.X_val, dataset.y_val)
    test_metrics = evaluate_classification(result.model, dataset.X_test, dataset.y_test)
    importances = feature_importance(result.model, dataset.feature_columns)
    sweep_thresholds = (
        [float(t) for t in args.thresholds.split(",")] if getattr(args, "thresholds", None) else None
    )
    sweep = sweep_signal_thresholds(result.model, dataset.X_val, dataset.y_val, thresholds=sweep_thresholds)

    print(f"Trained model_id={result.model_id}")
    print(f"lookahead_period={cfg.lookahead_period} bars, target_return_threshold={cfg.target_return_threshold}")
    print(f"features={len(dataset.feature_columns)}, multi_timeframe={higher_timeframes or 'none'}")
    print("Validation metrics:", json.dumps(val_metrics.as_dict(), indent=2))
    print("Test metrics:", json.dumps(test_metrics.as_dict(), indent=2))
    if sweep:
        print(
            "Threshold sweep (validation only -- precision if you only BUY when P(up) >= threshold; "
            "n_signals is how many val-set bars would have fired at that threshold):"
        )
        print(json.dumps(sweep, indent=2))
    print("Top features:", json.dumps(importances[:10], indent=2))


def cmd_walk_forward(args) -> None:
    cfg = settings
    overrides = {}
    if args.lookahead_period is not None:
        overrides["lookahead_period"] = args.lookahead_period
    if args.target_return_threshold is not None:
        overrides["target_return_threshold"] = args.target_return_threshold
    if overrides:
        cfg = settings.model_copy(update=overrides)

    try:
        clean = _load_market_data(args.symbol, args.timeframe, getattr(args, "use_cached", False))
    except (DownloadError, DataValidationError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    window_mode = getattr(args, "window_mode", "rolling")
    purge_bars = getattr(args, "purge_bars", None)
    results = run_walk_forward(
        clean,
        symbol=args.symbol,
        timeframe=args.timeframe,
        model_type=args.model_type,
        train_bars=args.train_bars,
        test_bars=args.test_bars,
        step_bars=args.step_bars,
        cfg=cfg,
        window_mode=window_mode,
        purge_bars=purge_bars,
    )

    if not results:
        print(
            "ERROR: No windows produced -- not enough usable bars for the requested "
            f"train_bars={args.train_bars} + test_bars={args.test_bars}. Try smaller windows "
            "or download more history.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"lookahead_period={cfg.lookahead_period} bars, target_return_threshold={cfg.target_return_threshold}")
    purge = cfg.lookahead_period if purge_bars is None else purge_bars
    print(
        f"{len(results)} walk-forward windows (mode={window_mode}, train_bars={args.train_bars}, "
        f"purge_bars={purge}, test_bars={args.test_bars}):\n"
    )
    for r in results:
        print(json.dumps(r.__dict__, indent=2))

    returns = [r.total_return_pct for r in results]
    profitable_windows = sum(1 for r in returns if r > 0)
    avg_return = sum(returns) / len(returns)
    print("\n--- Summary across windows ---")
    print(json.dumps({
        "windows": len(results),
        "profitable_windows": profitable_windows,
        "profitable_window_pct": round(100 * profitable_windows / len(results), 2),
        "avg_total_return_pct": round(avg_return, 4),
        "best_window_return_pct": round(max(returns), 4),
        "worst_window_return_pct": round(min(returns), 4),
    }, indent=2))


def cmd_evaluate_model(args) -> None:
    try:
        model = load_model_artifact(args.model_id)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Model {args.model_id} loaded successfully: {type(model).__name__}")


def cmd_paper_trade(args) -> None:
    """One paper-trading step for a saved account: run once per candle (e.g. daily via cron)."""
    from app.paper.evaluation import compute_norms, compute_targets, evaluate, reset_targets, targets_end
    from app.paper.norms import norms_status
    from app.paper.runner import (PaperRunConfig, account_settings, init_state, load_state, resume, run_step,
                                  save_state, state_path)
    from app.utils.time import utc_now

    if getattr(args, "reset_targets", False) and (getattr(args, "set_targets", False) or not getattr(args, "reason", None)):
        print("ERROR: --reset-targets needs --reason \"...\" and can't be combined with --set-targets.", file=sys.stderr)
        sys.exit(1)
    path = state_path(args.account)
    created = not path.exists()
    if path.exists():
        state = load_state(path)
        c = state.config
        if (args.symbol, args.timeframe, args.strategy) != (c.symbol, c.timeframe, c.strategy) and args.explicit:
            print(
                f"ERROR: account '{args.account}' already trades {c.symbol} {c.timeframe} {c.strategy}. "
                "Use a different --account name for a different setup.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        try:
            state = init_state(PaperRunConfig(args.account, args.symbol, args.timeframe, args.strategy,
                                              args.starting_balance, getattr(args, "fee_bps", 0.0) or 0.0))
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"Created paper account '{args.account}': {args.symbol} {args.timeframe} {args.strategy}, "
              f"starting balance {args.starting_balance:.2f}")

    if getattr(args, "resume", False):
        if state.halted is None:
            print(f"Account '{args.account}' is not halted; nothing to resume.")
        else:
            print(f"Was halted: {state.halted}")
            print(resume(state, utc_now()))
            save_state(state, path)

    c = state.config
    try:
        # One download serves both the targets backtest and the trading step.
        candles = _load_market_data(c.symbol, c.timeframe, getattr(args, "use_cached", False))
    except (DownloadError, DataValidationError, FileNotFoundError) as exc:
        print(f"ERROR: {exc} (state unchanged)", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "reset_targets", False):
        from app.data.validator import closed_candles

        if created:
            print("ERROR: a new account gets its targets automatically; there is nothing to reset.", file=sys.stderr)
            sys.exit(1)
        old = state.targets
        try:
            t = reset_targets(state, closed_candles(candles, c.timeframe), args.reason, getattr(args, "new_window", False))
        except ValueError as exc:
            print(f"ERROR: {exc} (state unchanged)", file=sys.stderr)
            sys.exit(1)
        print(f"Targets reset ({t['reset_reason']}):")
        for key, label in [("source", "window"), ("backtest_trades", "trades"), ("backtest_return_pct", "return %"),
                           ("profit_factor", "profit factor"), ("win_rate_pct", "win rate %"),
                           ("max_drawdown_pct", "max drawdown %"), ("avg_hold_bars", "avg trade bars")]:
            print(f"  {label:15} {old.get(key)} -> {t.get(key)}")

    want_targets = created or getattr(args, "set_targets", False)
    if want_targets and state.targets is not None:
        print(f"Targets for '{args.account}' were already set ({state.targets.get('source')}) and are kept: "
              "they're fixed on purpose so results can't be judged against moved goalposts.")
    elif want_targets:
        from app.data.validator import closed_candles

        state.targets = compute_targets(closed_candles(candles, c.timeframe), c.symbol, c.timeframe, c.strategy,
                                        account_settings(c))
        t = state.targets
        print(f"Targets set from {t['source']} ({t['backtest_trades']} trades): profit factor "
              f"{t['profit_factor']}, win rate {t['win_rate_pct']}%, max drawdown {t['max_drawdown_pct']}%, "
              f"avg trade {t['avg_hold_bars']} bars.")
    elif state.targets is None:
        print(f"No pass/fail targets yet: add them once with "
              f"python -m app.cli paper-trade --account {args.account} --set-targets")

    if state.targets and state.norms is None:
        # Reference figures for the normal-losses check, from the same backtest window as the
        # targets. Unlike the targets they don't judge anything, so adding them later is fine.
        import pandas as pd

        from app.data.validator import closed_candles

        hist = closed_candles(candles, c.timeframe)
        end = targets_end(state.targets)
        if end is not None:
            hist = hist[pd.to_datetime(hist["timestamp"], utc=True) < end + pd.Timedelta(days=1)]
        state.norms = compute_norms(hist, c.symbol, c.timeframe, c.strategy, account_settings(c))
        n = state.norms
        print(f"Normal-losses reference set from the same backtest: longest losing streak "
              f"{n['max_losing_streak']}, daily loss limit hit on {n['daily_limit_days']} of {n['days']} days, "
              f"worst week {n['worst_week_pct']}%.")

    try:
        result = run_step(state, candles=candles)
    except (DownloadError, DataValidationError) as exc:
        print(f"ERROR: {exc} (state unchanged)", file=sys.stderr)
        sys.exit(1)
    save_state(state, path)

    acct = state.account
    print(f"Processed {result.new_candles} new closed candle(s); latest {result.latest_candle} "
          f"close {result.latest_close}, signal {result.latest_signal}.")
    for e in result.events:
        print("  " + e)
    if not result.events:
        print("  No trades this run.")
    for p in acct.open_positions.values():
        unreal = (result.latest_close - p.entry_price) * p.size * (1 if p.direction == "BUY" else -1)
        print(f"Open: {p.direction} {p.size:.6f} {p.symbol} @ {p.entry_price:.2f} (stop {p.stop_price:.2f}, "
              f"target {p.target_price:.2f}), unrealized {unreal:+.2f}")
    closed = acct.trade_history
    wins = sum(1 for t in closed if t.pnl > 0)
    print(f"Balance {acct.balance:.2f} (started {state.config.starting_balance:.2f}); "
          f"{len(closed)} closed trade(s), {wins} winning. State: {path}")
    if state.targets:
        print(f"Evaluation vs backtest: {evaluate(state)['verdict']}")
    if state.norms:
        print(f"Normal-losses check: {norms_status(state)['verdict']}")
    if state.halted:
        print(f"HALTED since {state.halted}. Open positions still run to their stop/target, but no new "
              f"trades open. To restart: python -m app.cli paper-trade --account {args.account} --resume",
              file=sys.stderr)


def cmd_paper_snapshot(args) -> None:
    """Write snapshot.json and history.json for the hosted status page (read-only: never trades)."""
    from pathlib import Path

    from app.paper import snapshot
    from app.paper.runner import list_states
    from app.paper.summary import summarize
    from app.utils.time import utc_now

    now = utc_now()
    summaries = summarize(list_states())
    if not summaries:
        print("ERROR: no paper accounts found in data/paper/.", file=sys.stderr)
        sys.exit(1)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    snapshot.append_history(summaries, now)
    (out / "snapshot.json").write_text(json.dumps(snapshot.build_snapshot(summaries, now)))
    (out / "history.json").write_text(json.dumps(snapshot.history_doc(now=now)))
    sizes = {f: (out / f).stat().st_size for f in ("snapshot.json", "history.json")}
    print(f"Wrote {out}/snapshot.json ({sizes['snapshot.json']:,} bytes) and history.json "
          f"({sizes['history.json']:,} bytes); history point appended to {snapshot.HISTORY_PATH}")


def cmd_research(args) -> None:
    """Rerun the market and settings-sensitivity studies and save them for the dashboard."""
    from app.research import studies

    if not args.use_cached:
        for symbol, _name, start in studies.MARKETS:
            for timeframe, kw in (("1h", {}), ("1d", {"start": "2016-01-01"})):
                try:
                    raw = download_ohlcv(symbol=symbol, timeframe=timeframe, **kw)
                    clean, _ = validate_and_clean(raw, timeframe=timeframe, symbol=symbol)
                except (DownloadError, DataValidationError) as exc:
                    print(f"ERROR: {exc} -- re-run with --use-cached to use the existing cache.", file=sys.stderr)
                    sys.exit(1)
                merged, _, _ = merge_into_cache(clean, symbol, timeframe)
                print(f"{symbol} {timeframe}: {len(merged)} candles cached")
    try:
        results = studies.run_all(workers=args.workers)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    path = studies.save(results)
    print(f"Saved {len(results['markets'])} market results and {len(results['sensitivity'])} sensitivity "
          f"studies to {path}")


def cmd_system_status(args) -> None:
    print(json.dumps({
        "app": settings.app_name,
        "environment": settings.environment,
        "market_symbol": settings.market_symbol,
        "timeframe": settings.timeframe,
        "live_trading_enabled": settings.live_trading_enabled,
        "available_brokers": list(AVAILABLE_BROKERS),
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli", description="SPARKZ TRADER CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("download-data")
    p.add_argument("--symbol", default=settings.market_symbol)
    p.add_argument("--timeframe", default=settings.timeframe)
    p.set_defaults(func=cmd_download_data)

    p = sub.add_parser("backtest")
    p.add_argument("--symbol", default=settings.market_symbol)
    p.add_argument("--timeframe", default=settings.timeframe)
    p.add_argument("--strategy", default="baseline", help="'baseline', 'baseline_long_only', or a model_id from train-model.")
    p.add_argument(
        "--buy-threshold", type=float, default=None, dest="buy_threshold",
        help=f"Model-strategy only: P(up) needed to fire a BUY (default from settings: {settings.signal_buy_threshold}). "
             "Use train-model's threshold sweep to find a value the model's probabilities actually reach.",
    )
    p.add_argument(
        "--sell-threshold", type=float, default=None, dest="sell_threshold",
        help=f"Model-strategy only: P(down) needed to fire a SELL (default from settings: {settings.signal_sell_threshold}).",
    )
    p.add_argument(
        "--lookahead-period", type=int, default=None, dest="lookahead_period",
        help="Model-strategy only: should match what the model was trained with (train-model's --lookahead-period).",
    )
    p.add_argument(
        "--target-return-threshold", type=float, default=None, dest="target_return_threshold",
        help="Model-strategy only: should match what the model was trained with.",
    )
    p.add_argument(
        "--full-history", action="store_true", dest="full_history",
        help="Model-strategy only: backtest the ENTIRE downloaded history including the model's own "
             "training data, instead of just the held-out test period. This inflates results with "
             "memorization and is NOT a valid performance estimate -- for debugging/curiosity only.",
    )
    p.add_argument(
        "--multi-timeframe", type=str, default=None, dest="multi_timeframe",
        help="Model-strategy only: must match what the model was trained with, e.g. '4h,1d'.",
    )
    p.add_argument(
        "--use-cached", action="store_true", dest="use_cached",
        help="Read the local cache written by `download-data` instead of downloading. Works offline; "
             "use it when Yahoo is down or rate-limiting.",
    )
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("train-model")
    p.add_argument("--symbol", default=settings.market_symbol)
    p.add_argument("--timeframe", default=settings.timeframe)
    p.add_argument("--model-type", default="random_forest", dest="model_type")
    p.add_argument(
        "--lookahead-period", type=int, default=None, dest="lookahead_period",
        help=f"Bars ahead the label looks (default from settings: {settings.lookahead_period}).",
    )
    p.add_argument(
        "--target-return-threshold", type=float, default=None, dest="target_return_threshold",
        help=f"Min future return counted as 'up' (default from settings: {settings.target_return_threshold}).",
    )
    p.add_argument(
        "--thresholds", type=str, default=None,
        help="Comma-separated probability thresholds for the sweep, e.g. '0.15,0.20,0.25,0.30'. "
             "Defaults to 0.30-0.75 in steps of 0.05.",
    )
    p.add_argument(
        "--multi-timeframe", type=str, default=None, dest="multi_timeframe",
        help="Comma-separated higher timeframes to add trend/momentum/volatility context from, "
             "e.g. '4h,1d'. Leakage-safe: a higher-timeframe bar's indicators only become visible "
             "once that bar has actually closed.",
    )
    p.add_argument(
        "--use-cached", action="store_true", dest="use_cached",
        help="Read the local cache written by `download-data` instead of downloading. Works offline; "
             "use it when Yahoo is down or rate-limiting.",
    )
    p.set_defaults(func=cmd_train_model)

    p = sub.add_parser("walk-forward", help="Slide a train/test window across history to check if a model's edge is consistent over time, not just one lucky split.")
    p.add_argument("--symbol", default=settings.market_symbol)
    p.add_argument("--timeframe", default=settings.timeframe)
    p.add_argument("--model-type", default="random_forest", dest="model_type")
    p.add_argument("--train-bars", type=int, default=2000, dest="train_bars")
    p.add_argument("--test-bars", type=int, default=500, dest="test_bars")
    p.add_argument("--step-bars", type=int, default=None, dest="step_bars", help="Defaults to test-bars (non-overlapping windows).")
    p.add_argument(
        "--window-mode", choices=["rolling", "expanding"], default="rolling", dest="window_mode",
        help="'rolling': every window trains on the last --train-bars bars. 'expanding': every window "
             "trains on all history so far (--train-bars is the first window's size).",
    )
    p.add_argument(
        "--purge-bars", type=int, default=None, dest="purge_bars",
        help="Bars skipped between each train and test segment so no training label peeks into the "
             "test period. Defaults to the lookahead period.",
    )
    p.add_argument("--lookahead-period", type=int, default=None, dest="lookahead_period")
    p.add_argument("--target-return-threshold", type=float, default=None, dest="target_return_threshold")
    p.add_argument(
        "--use-cached", action="store_true", dest="use_cached",
        help="Read the local cache written by `download-data` instead of downloading. Works offline; "
             "use it when Yahoo is down or rate-limiting.",
    )
    p.set_defaults(func=cmd_walk_forward)

    p = sub.add_parser("evaluate-model")
    p.add_argument("--model-id", required=True, dest="model_id")
    p.set_defaults(func=cmd_evaluate_model)

    p = sub.add_parser(
        "paper-trade",
        help="Run one paper-trading step for a saved account (state in data/paper/<account>.json). "
             "Run it once per candle, e.g. daily from cron for a 1d strategy.",
    )
    p.add_argument("--account", default="default")
    p.add_argument("--symbol", default=settings.market_symbol)
    p.add_argument("--timeframe", default=settings.timeframe)
    p.add_argument("--strategy", default="baseline", help="'baseline' or 'baseline_long_only'.")
    p.add_argument("--starting-balance", type=float, default=settings.initial_capital, dest="starting_balance",
                   help="Only used when the account is first created.")
    p.add_argument("--use-cached", action="store_true", dest="use_cached",
                   help="Read the local cache instead of downloading (no network).")
    p.add_argument("--fee-bps", type=float, default=0.0, dest="fee_bps",
                   help="Only used when the account is first created: exchange/broker fee per side in basis "
                        "points of price (10 = 0.1%%), charged on top of spread and slippage in paper fills "
                        "and in the targets backtest.")
    p.add_argument("--set-targets", action="store_true", dest="set_targets",
                   help="Fix this account's pass/fail targets from a backtest of the same setup (only if none "
                        "are set yet; new accounts get them automatically).")
    p.add_argument("--reset-targets", action="store_true", dest="reset_targets",
                   help="REPLACE this account's targets (and normal-losses figures) with a new backtest over the "
                        "same data window, e.g. after a backtester bug fix. The old ones are kept under "
                        "'previous'. Requires --reason. Deliberately separate from --set-targets.")
    p.add_argument("--reason", help="Why the targets are being reset (stored with them). Used with --reset-targets.")
    p.add_argument("--new-window", action="store_true", dest="new_window",
                   help="With --reset-targets: backtest over the latest data instead of the old window.")
    p.add_argument("--resume", action="store_true",
                   help="Restart an account halted by the max-drawdown limit: its current balance becomes the "
                        "new peak the limit is measured from.")
    p.set_defaults(func=cmd_paper_trade)

    p = sub.add_parser("paper-snapshot", help="Write the paper accounts' status as JSON for the hosted status page.")
    p.add_argument("--out-dir", required=True, dest="out_dir")
    p.set_defaults(func=cmd_paper_snapshot)

    p = sub.add_parser("research", help="Rerun the market and settings-sensitivity studies for the dashboard.")
    p.add_argument("--use-cached", action="store_true", dest="use_cached",
                   help="Skip downloading and use the local data cache as-is.")
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(func=cmd_research)

    p = sub.add_parser("system-status")
    p.set_defaults(func=cmd_system_status)

    args = parser.parse_args()
    # For paper-trade: only complain about a setup mismatch if the user actually
    # passed those flags, not when they're just the defaults.
    args.explicit = any(a in sys.argv for a in ("--symbol", "--timeframe", "--strategy"))
    args.func(args)


if __name__ == "__main__":
    main()
