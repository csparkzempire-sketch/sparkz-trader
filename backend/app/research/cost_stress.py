"""
Cost stress test for the hourly paper accounts: does their backtest edge survive
higher trading costs?

The hourly strategies passed the random-entry check, but much of their return
comes in the first hour or two after a signal, so spread and slippage eat into
it directly. Each account's targets backtest (same data window, rules, stops and
sizing) is re-run with spread and slippage multiplied by MULTIPLIERS. Costs are
charged on entry and on exit.

PASS, fixed before running: at 2x the modeled costs the account still has
  - a net return above 0,
  - a profit factor of at least 1.05, and
  - at least 60% of its calendar quarters (those with >= 10 closed trades) profitable.

Also reported: the round-trip cost in basis points at 1x, the break-even cost
multiple (interpolated where net return crosses 0), and P&L by quarter at 1x and 2x.

  python -m app.research.cost_stress [--paper-dir DIR] [--use-cached]  -> app/research/cost_stress.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.metrics import compute_metrics
from app.config import settings
from app.data.validator import closed_candles
from app.paper.evaluation import _features, targets_history
from app.paper.runner import PAPER_DIR, load_state
from app.risk.risk_manager import RiskManager

OUT = Path(__file__).with_name("cost_stress.json")
MULTIPLIERS = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0)
PASS_MULT = 2.0
MIN_PF = 1.05
MIN_QUARTER_SHARE = 0.6
MIN_QUARTER_TRADES = 10


def run_at(f: pd.DataFrame, symbol: str, timeframe: str, mult: float, fee_bps: float = 0.0):
    # As in the targets backtest. The multiplier scales spread and slippage; an account's own fee stays fixed.
    cfg = settings.model_copy(update={"max_drawdown_pct": 0.5, "fee_bps": fee_bps})
    bt = BacktestConfig.from_settings(cfg, symbol, timeframe)
    bt = dataclasses.replace(bt, spread_pips=bt.spread_pips * mult, slippage_pips=bt.slippage_pips * mult)
    return BacktestEngine(bt, risk_manager=RiskManager(cfg)).run(f), bt


def by_quarter(result) -> dict[str, dict]:
    rows = [(pd.Timestamp(t.exit_time).tz_convert("UTC").tz_localize(None).to_period("Q"), t.pnl)
            for t in result.portfolio.closed_trades]
    out: dict[str, dict] = {}
    for q, pnl in rows:
        d = out.setdefault(str(q), {"trades": 0, "net_pnl": 0.0})
        d["trades"] += 1
        d["net_pnl"] += pnl
    return out


def break_even(mults, returns) -> float | None:
    """Cost multiple where net return crosses zero (linear interpolation). 0.0 if it already loses
    at 1x; None if it is still profitable at the highest multiple tested."""
    if returns[0] <= 0:
        return 0.0
    for m0, r0, m1, r1 in zip(mults, returns, mults[1:], returns[1:]):
        if r0 > 0 >= r1:
            return float(m0 + (m1 - m0) * r0 / (r0 - r1))
    return None


def check_account(path: Path, use_cached: bool) -> dict:
    from app.cli import _load_market_data

    st = load_state(path)
    c = st.config
    candles = closed_candles(_load_market_data(c.symbol, c.timeframe, use_cached), c.timeframe)
    hist = targets_history(candles, st.targets, c.timeframe, c.strategy) if st.targets else None
    f = _features(candles if hist is None else hist, c.strategy, settings)

    runs = {}
    for m in MULTIPLIERS:
        res, bt = run_at(f, c.symbol, c.timeframe, m, c.fee_bps)
        met = compute_metrics(res.portfolio, c.timeframe, c.symbol)
        pf = met.profit_factor if isinstance(met.profit_factor, float) and np.isfinite(met.profit_factor) else None
        runs[m] = {"return_pct": met.total_return_pct, "profit_factor": pf, "trades": met.total_trades,
                   "win_rate_pct": met.win_rate_pct, "max_drawdown_pct": met.max_drawdown_pct,
                   "quarters": by_quarter(res)}
        if m == 1.0:
            mid = float(f["close"].median())
            round_trip_bp = 2 * bt.spread_pips * bt.pip_size + 2 * bt.slippage_pips * bt.pip_size
            round_trip_bp = round_trip_bp / mid * 1e4

    stressed = runs[PASS_MULT]
    qs = [q for q in stressed["quarters"].values() if q["trades"] >= MIN_QUARTER_TRADES]
    share = (sum(q["net_pnl"] > 0 for q in qs) / len(qs)) if qs else 0.0
    checks = {
        "net_return_positive": bool(stressed["return_pct"] > 0),
        "profit_factor_at_least_1.05": bool((stressed["profit_factor"] or 0) >= MIN_PF),
        "quarters_profitable_60pct": bool(share >= MIN_QUARTER_SHARE),
    }
    be = break_even(list(MULTIPLIERS), [runs[m]["return_pct"] for m in MULTIPLIERS])
    return {
        "account": c.account_name, "symbol": c.symbol, "timeframe": c.timeframe, "strategy": c.strategy,
        "window": [str(f["timestamp"].iloc[0]), str(f["timestamp"].iloc[-1])],
        "round_trip_cost_bp_at_1x": round(round_trip_bp, 2),
        "break_even_multiplier": None if be is None else round(be, 2),
        "break_even_round_trip_bp": None if be is None else round(be * round_trip_bp, 1),
        "quarters_profitable_at_2x": f"{sum(q['net_pnl'] > 0 for q in qs)}/{len(qs)}",
        "runs": {str(m): r for m, r in runs.items()},
        "checks": checks, "passed": all(checks.values()),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--paper-dir", default=str(PAPER_DIR))
    p.add_argument("--use-cached", action="store_true")
    a = p.parse_args()
    results = []
    for path in sorted(Path(a.paper_dir).glob("*.json")):
        if load_state(path).config.timeframe != "1h":
            continue
        r = check_account(path, a.use_cached)
        results.append(r)
        rr = " ".join(f"{m}x:{r['runs'][str(m)]['return_pct']:+.0f}%" for m in MULTIPLIERS)
        print(f"{r['account']:20} {rr}  break-even {r['break_even_multiplier']}x -> {'PASS' if r['passed'] else 'FAIL'}", flush=True)
        OUT.write_text(json.dumps({"multipliers": MULTIPLIERS, "accounts": results}, indent=1, default=str))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
