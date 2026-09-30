"""
Pass/fail evaluation of a paper account against its own backtest.

A good backtest isn't evidence until live (paper) results agree with it. To
keep that judgment honest, each account's targets are computed ONCE from a
backtest of the exact same setup (symbol, timeframe, strategy, costs) and
saved into the account's state file -- fixed before the results arrive, so
nobody can quietly move the goalposts afterwards.

Checks, and why each is loose rather than "match the backtest exactly":
- profit factor >= 1.0: the backtests sit around 1.2-2.0, but 30 trades is a
  small sample; below 1.0 means the account is simply losing.
- win rate within +/-10 points of the backtest: a much lower OR higher win
  rate means the strategy is behaving differently from what was tested.
- max drawdown no worse than the backtest's: checked from the first trade on,
  since a drawdown beyond anything the backtest saw is a warning in itself.
- average holding time between 0.5x and 2x the backtest's: trades that last
  far shorter or longer than tested suggest something differs in execution.

Nothing is judged (except drawdown) until MIN_TRADES trades have closed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.metrics import compute_metrics
from app.config import Settings, settings
from app.features.feature_engineering import build_feature_matrix
from app.risk.risk_manager import RiskManager
from app.strategy.rules import rule_signal
from app.utils.time import timeframe_to_pandas_freq, utc_now

MIN_TRADES = 30
WIN_RATE_TOLERANCE_PTS = 10.0
HOLD_RATIO_RANGE = (0.5, 2.0)


def compute_targets(candles: pd.DataFrame, symbol: str, timeframe: str, strategy: str,
                    cfg: Settings | None = None) -> dict:
    """Backtest this exact setup on `candles` (validated OHLCV, closed candles only)
    and return the figures the paper account will be held to. The drawdown limit is
    relaxed to 50% here so the backtest isn't cut short by a halt -- the targets
    should describe how the strategy trades, not where a safety stop kicked in."""
    cfg = (cfg or settings).model_copy(update={"max_drawdown_pct": 0.5})
    f = build_feature_matrix(candles, cfg)
    f["signal"] = rule_signal(f, strategy, cfg)
    f = f.dropna(subset=["atr"]).reset_index(drop=True)
    result = BacktestEngine(BacktestConfig.from_settings(cfg, symbol, timeframe), risk_manager=RiskManager(cfg)).run(f)
    m = compute_metrics(result.portfolio, timeframe, symbol)
    pf = m.profit_factor if isinstance(m.profit_factor, float) and m.profit_factor != float("inf") else None
    return {
        "source": f"backtest {f['timestamp'].iloc[0]:%Y-%m-%d} to {f['timestamp'].iloc[-1]:%Y-%m-%d}",
        "set_at": utc_now().isoformat(timespec="seconds"),
        "backtest_trades": m.total_trades,
        "backtest_return_pct": m.total_return_pct,
        "profit_factor": pf,
        "win_rate_pct": m.win_rate_pct,
        "max_drawdown_pct": m.max_drawdown_pct,  # negative, e.g. -24.0
        "avg_hold_bars": m.average_holding_bars,
    }


@dataclass
class Check:
    name: str
    target: str
    actual: str
    status: str  # "pass" | "fail" | "pending"


def _realized_drawdown_pct(starting_balance: float, pnls: list[float]) -> float:
    balance = peak = starting_balance
    worst = 0.0
    for pnl in pnls:
        balance += pnl
        peak = max(peak, balance)
        worst = min(worst, (balance - peak) / peak * 100)
    return worst


def evaluate(state) -> dict:
    """Score a PaperRunState against its saved targets."""
    t = state.targets
    trades = state.account.trade_history
    n = len(trades)
    if not t:
        return {"verdict": "No targets set", "closed_trades": n, "min_trades": MIN_TRADES, "checks": []}

    enough = n >= MIN_TRADES
    checks: list[Check] = []

    pnls = [x.pnl for x in trades]
    wins = sum(1 for p in pnls if p > 0)
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p <= 0)
    pf = gross_win / gross_loss if gross_loss > 0 else None

    checks.append(Check(
        "Profit factor", "≥ 1.00" + (f" (backtest {t['profit_factor']:.2f})" if t.get("profit_factor") else ""),
        "—" if not n else ("no losses yet" if pf is None else f"{pf:.2f}"),
        "pending" if not enough else ("pass" if pf is None or pf >= 1.0 else "fail"),
    ))

    wr = wins / n * 100 if n else None
    lo, hi = t["win_rate_pct"] - WIN_RATE_TOLERANCE_PTS, t["win_rate_pct"] + WIN_RATE_TOLERANCE_PTS
    checks.append(Check(
        "Win rate", f"{max(lo, 0):.0f}–{min(hi, 100):.0f}% (backtest {t['win_rate_pct']:.1f}%)",
        "—" if wr is None else f"{wr:.1f}%",
        "pending" if not enough else ("pass" if lo <= wr <= hi else "fail"),
    ))

    dd = _realized_drawdown_pct(state.config.starting_balance, pnls)
    checks.append(Check(
        "Max drawdown", f"no worse than {t['max_drawdown_pct']:.1f}%",
        f"{dd:.1f}%",
        "fail" if dd < t["max_drawdown_pct"] else ("pass" if enough else "pending"),
    ))

    bar_s = pd.Timedelta(timeframe_to_pandas_freq(state.config.timeframe)).total_seconds()
    hold = (sum((x.closed_at - x.opened_at).total_seconds() for x in trades) / n / bar_s) if n else None
    a, b = t["avg_hold_bars"] * HOLD_RATIO_RANGE[0], t["avg_hold_bars"] * HOLD_RATIO_RANGE[1]
    checks.append(Check(
        "Avg trade length", f"{a:.1f}–{b:.1f} bars (backtest {t['avg_hold_bars']:.1f})",
        "—" if hold is None else f"{hold:.1f} bars",
        "pending" if not enough else ("pass" if a <= hold <= b else "fail"),
    ))

    failed = [c.name for c in checks if c.status == "fail"]
    if failed:
        verdict = "Failing: " + ", ".join(failed)
    elif not enough:
        verdict = f"Collecting trades ({n}/{MIN_TRADES})"
    else:
        verdict = "Passing"
    return {
        "verdict": verdict,
        "closed_trades": n,
        "min_trades": MIN_TRADES,
        "targets_source": t.get("source"),
        "checks": [asdict(c) for c in checks],
    }
