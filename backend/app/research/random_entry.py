"""
Random-entry check for the paper accounts: do their strategies time the market
better than chance, or does their backtest profit just come from the market's
direction over the sample?

For each account, its own targets backtest (same data window, costs, stops and
targets) is re-run with the signal column replaced:

  random timing     the real signal series circularly shifted by a random offset: the
                    same signals, in the same runs of consecutive bars, so about the
                    same number of trades, but out of step with prices. Tests timing.
                    (A plain shuffle would scatter the runs into many more separate
                    entries and charge the random side for ~60% more trades.)
  random direction  (buy-and-sell accounts only) signals at the real moments, but each
                    run of consecutive signals gets BUY or SELL by coin flip. Tests the
                    choice of direction.

Random runs' trade counts are recorded next to the real one so the comparison
can be checked for like-for-like activity.

PASS, fixed before running: the real net return beats the 95th percentile of
every random test that applies to the account. Anything less means the
strategy's backtest result is what random entries with the same exits would
also have produced, and a paper "pass" wouldn't show skill.

  python -m app.research.random_entry [--runs 200]  -> app/research/random_entry.json
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.config import settings
from app.data.validator import closed_candles
from app.paper.evaluation import _backtest, targets_end
from app.paper.runner import PAPER_DIR, load_state
from app.risk.risk_manager import RiskManager

OUT = Path(__file__).with_name("random_entry.json")
_W: dict = {}


def _net_return(result, initial: float) -> float:
    return (result.portfolio.cash / initial - 1) * 100


def _init(f: pd.DataFrame, symbol: str, timeframe: str) -> None:
    cfg = settings.model_copy(update={"max_drawdown_pct": 0.5})    # as in the targets backtest
    _W.update(f=f, cfg=cfg, bt=BacktestConfig.from_settings(cfg, symbol, timeframe))


def _run(signal: np.ndarray) -> tuple[float, int]:
    g = _W["f"].copy()
    g["signal"] = signal
    res = BacktestEngine(_W["bt"], risk_manager=RiskManager(_W["cfg"])).run(g)
    return _net_return(res, _W["bt"].initial_capital), len(res.portfolio.closed_trades)


def random_timing(signal: pd.Series, eligible: np.ndarray, rng) -> np.ndarray:
    """Circularly shift the signals within the eligible (warmed-up) bars by a random offset of
    10-90% of their length: identical structure, misaligned with prices."""
    out = signal.to_numpy().copy()
    idx = np.flatnonzero(eligible)
    n = len(idx)
    shift = int(rng.integers(max(1, n // 10), max(2, n * 9 // 10)))
    out[idx] = np.roll(out[idx], shift)
    return out


def random_direction(signal: pd.Series, rng) -> np.ndarray:
    """Same moments; each run of consecutive signal bars gets one random direction."""
    out = signal.to_numpy().copy()
    fire = np.isin(out, ["BUY", "SELL"])
    run_id = np.cumsum(np.concatenate([[fire[0]], fire[1:] & ~fire[:-1]]))
    coin = np.where(rng.random(run_id.max() + 1) < 0.5, "BUY", "SELL")
    out[fire] = coin[run_id[fire]]
    return out


def targets_window(candles: pd.DataFrame, targets: dict | None, symbol: str, timeframe: str, strategy: str):
    """Cut cached candles to the history the account's targets backtest used. The targets record
    the backtest's first and last day, after indicator warm-up; the cache can reach further back
    (daily bars go back to 2019), so the start is found by adding back the warm-up bars that
    make the backtest begin on the recorded day. Returns (real backtest, features)."""
    ts = pd.to_datetime(candles["timestamp"], utc=True)
    end = targets_end(targets) if targets else None
    if end is not None:
        candles, ts = candles[ts < end + pd.Timedelta(days=1)], ts[ts < end + pd.Timedelta(days=1)]
    try:
        start = pd.Timestamp(targets["source"].split(" to ")[0].removeprefix("backtest "), tz="UTC")
    except (TypeError, KeyError, ValueError):
        start = None
    if start is not None and ts.iloc[0] < start:
        first = int((ts < start).sum())
        for warmup in range(0, min(first, 200) + 1):
            real, f = _backtest(candles.iloc[first - warmup:], symbol, timeframe, strategy)
            if pd.Timestamp(f["timestamp"].iloc[0]).normalize() == start:
                return real, f
    return _backtest(candles, symbol, timeframe, strategy)


def check_account(path: Path, runs: int, use_cached: bool = True) -> dict:
    from app.cli import _load_market_data

    st = load_state(path)
    c = st.config
    candles = closed_candles(_load_market_data(c.symbol, c.timeframe, use_cached), c.timeframe)
    real, f = targets_window(candles, st.targets, c.symbol, c.timeframe, c.strategy)
    real_ret = _net_return(real, real.config.initial_capital)
    cols = [f"ema_{settings.ema_fast}", f"ema_{settings.ema_slow}", "rsi"]
    eligible = ~f[cols].isna().any(axis=1).to_numpy()
    rng = np.random.default_rng(abs(hash(c.account_name)) % 2**32)

    tests = {"random_timing": [random_timing(f["signal"], eligible, rng) for _ in range(runs)]}
    if c.strategy == "baseline":
        tests["random_direction"] = [random_direction(f["signal"], rng) for _ in range(runs)]

    workers = max(1, (os.cpu_count() or 2) - 1)
    out = {"account": c.account_name, "symbol": c.symbol, "timeframe": c.timeframe, "strategy": c.strategy,
           "window": [str(f["timestamp"].iloc[0]), str(f["timestamp"].iloc[-1])],
           "real_return_pct": real_ret, "real_trades": len(real.portfolio.closed_trades),
           "buy_and_hold_pct": float((f["close"].iloc[-1] / f["close"].iloc[0] - 1) * 100), "tests": {}}
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(f, c.symbol, c.timeframe)) as pool:
        for name, sigs in tests.items():
            pairs = list(pool.map(_run, sigs, chunksize=4))
            r = np.asarray([x[0] for x in pairs])
            trades = np.asarray([x[1] for x in pairs])
            out["tests"][name] = {
                "median_trades": float(np.median(trades)),
                "runs": len(r), "p5": float(np.percentile(r, 5)), "p50": float(np.percentile(r, 50)),
                "p95": float(np.percentile(r, 95)), "max": float(r.max()),
                "real_percentile": float((r < real_ret).mean() * 100),
                "passed": bool(real_ret > np.percentile(r, 95)),
            }
    out["passed"] = all(t["passed"] for t in out["tests"].values())
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", type=int, default=200)
    p.add_argument("--paper-dir", default=str(PAPER_DIR))
    a = p.parse_args()
    results = []
    for path in sorted(Path(a.paper_dir).glob("*.json")):
        r = check_account(path, a.runs)
        results.append(r)
        t = " ".join(f"{k}: {v['real_percentile']:.0f}th pct" for k, v in r["tests"].items())
        print(f"{r['account']:20} real {r['real_return_pct']:+8.1f}%  {t}  -> {'PASS' if r['passed'] else 'FAIL'}", flush=True)
        OUT.write_text(json.dumps({"runs": a.runs, "accounts": results}, indent=1))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
