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

Targets can be replaced later only with `paper-trade --reset-targets --reason "..."`
(reset_targets below), for when the backtest they came from was itself wrong. The old
targets stay inside the new ones under "previous", with the reason, so the change is on record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import comb

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


def _features(candles: pd.DataFrame, strategy: str, cfg: Settings) -> pd.DataFrame:
    f = build_feature_matrix(candles, cfg)
    f["signal"] = rule_signal(f, strategy, cfg)
    return f.dropna(subset=["atr"]).reset_index(drop=True)


def _backtest(candles: pd.DataFrame, symbol: str, timeframe: str, strategy: str, cfg: Settings | None = None):
    """Backtest this exact setup on `candles` (validated OHLCV, closed candles only). The
    drawdown limit is relaxed to 50% so the backtest isn't cut short by a halt: the figures
    should describe how the strategy trades, not where a safety stop kicked in."""
    cfg = (cfg or settings).model_copy(update={"max_drawdown_pct": 0.5})
    f = _features(candles, strategy, cfg)
    result = BacktestEngine(BacktestConfig.from_settings(cfg, symbol, timeframe), risk_manager=RiskManager(cfg)).run(f)
    return result, f


def compute_targets(candles: pd.DataFrame, symbol: str, timeframe: str, strategy: str,
                    cfg: Settings | None = None) -> dict:
    """The figures the paper account will be held to, from a backtest of the same setup."""
    result, f = _backtest(candles, symbol, timeframe, strategy, cfg)
    m = compute_metrics(result.portfolio, timeframe, symbol)
    pf = m.profit_factor if isinstance(m.profit_factor, float) and m.profit_factor != float("inf") else None
    return {
        "source": f"backtest {f['timestamp'].iloc[0]:%Y-%m-%d} to {f['timestamp'].iloc[-1]:%Y-%m-%d}",
        "last_bar": pd.Timestamp(f["timestamp"].iloc[-1]).isoformat(),  # exact end, for rebuilding the window
        "set_at": utc_now().isoformat(timespec="seconds"),
        "backtest_trades": m.total_trades,
        "backtest_return_pct": m.total_return_pct,
        "profit_factor": pf,
        "win_rate_pct": m.win_rate_pct,
        "max_drawdown_pct": m.max_drawdown_pct,  # negative, e.g. -24.0
        "avg_hold_bars": m.average_holding_bars,
    }


def compute_norms(candles: pd.DataFrame, symbol: str, timeframe: str, strategy: str,
                  cfg: Settings | None = None) -> dict:
    """The worst stretches the same backtest went through (app.paper.norms). Reference
    figures for the normal-losses check; they never affect the pass/fail verdict."""
    from app.paper.norms import norms_from_backtest

    result, f = _backtest(candles, symbol, timeframe, strategy, cfg)
    return norms_from_backtest(result, f["timestamp"], cfg)


def targets_end(targets: dict) -> pd.Timestamp | None:
    """Last day of the backtest the targets came from ("backtest 2021-10-13 to 2026-09-29")."""
    try:
        return pd.Timestamp(targets["source"].rsplit(" to ", 1)[1], tz="UTC")
    except (KeyError, IndexError, ValueError, AttributeError):
        return None


def targets_start(targets: dict) -> pd.Timestamp | None:
    """First day of the backtest the targets came from."""
    try:
        return pd.Timestamp(targets["source"].split(" to ", 1)[0].removeprefix("backtest "), tz="UTC")
    except (KeyError, IndexError, ValueError, AttributeError):
        return None


def targets_history(candles: pd.DataFrame, targets: dict, timeframe: str, strategy: str,
                    cfg: Settings | None = None) -> pd.DataFrame | None:
    """The slice of `candles` whose backtest covers exactly the targets' window ("backtest A to B"),
    indicator warm-up included, or None if the data no longer reaches back that far (Yahoo keeps
    about two years of hourly bars, so an hourly window drops out of reach a day at a time)."""
    start, end = targets_start(targets), targets_end(targets)
    if start is None or end is None:
        return None
    ts = pd.to_datetime(candles["timestamp"], utc=True)
    keep = ts < end + pd.Timedelta(days=1)
    # The window's dates alone don't say where its last day stopped: use the stored last bar, or for
    # older targets the bars that had closed when they were set.
    if targets.get("last_bar"):
        keep &= ts <= pd.Timestamp(targets["last_bar"])
    elif targets.get("set_at"):
        keep &= ts + pd.Timedelta(timeframe_to_pandas_freq(timeframe)) <= pd.Timestamp(targets["set_at"])
    candles, ts = candles[keep], ts[keep]
    first = int((ts < start).sum())
    for warmup in range(0, min(first, 300) + 1):
        hist = candles.iloc[first - warmup:]
        f = _features(hist, strategy, cfg or settings)
        if f.empty:
            continue
        day0 = pd.Timestamp(f["timestamp"].iloc[0]).normalize()
        if day0 < start:
            return None
        if day0 == start:
            return hist if pd.Timestamp(f["timestamp"].iloc[-1]).normalize() == end else None
    return None


def reset_targets(state, candles: pd.DataFrame, reason: str, new_window: bool = False) -> dict:
    """Replace an account's targets (and normal-losses norms) with a fresh backtest of the same
    setup, keeping the old ones under "previous" with `reason`. By default over the SAME data window,
    so only the backtest itself changes (e.g. after a bug fix); new_window=True uses all of `candles`.
    Raises ValueError if there are no targets yet or the old window can't be rebuilt."""
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reset targets.")
    if not state.targets:
        raise ValueError("This account has no targets yet; set them with --set-targets.")
    from app.paper.runner import account_settings

    c = state.config
    cfg = account_settings(c)
    hist = candles if new_window else targets_history(candles, state.targets, c.timeframe, c.strategy, cfg)
    if hist is None:
        raise ValueError(
            f"The downloaded data no longer covers the current targets' window ({state.targets.get('source')}). "
            "Add --new-window to recompute over the latest data instead."
        )
    new = compute_targets(hist, c.symbol, c.timeframe, c.strategy, cfg)
    if not new_window and new["source"] != state.targets.get("source"):
        raise ValueError(f"Rebuilt window {new['source']!r} differs from {state.targets.get('source')!r}; "
                         "add --new-window to recompute over the latest data instead.")
    new["reset_reason"] = reason.strip()
    new["previous"] = state.targets
    norms = compute_norms(hist, c.symbol, c.timeframe, c.strategy, cfg)
    if state.norms:
        norms["previous"] = state.norms
    state.targets, state.norms = new, norms
    return new


def _share(p: float) -> str:
    return "under 0.1%" if p < 0.001 else f"{p:.1%}" if p < 0.1 else f"{p:.0%}"


def early_check(wins: int, n: int, win_rate_pct: float) -> dict | None:
    """How unusual the paper win count is for the backtest's win rate, before MIN_TRADES is reached.
    Binomial tail probability of a result at least this far from expected, on the side it fell.
    Information only: it never changes the verdict."""
    if n == 0 or not win_rate_pct:
        return None
    p = win_rate_pct / 100
    pmf = [comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(n + 1)]
    expected = n * p
    low = wins <= expected
    tail = sum(pmf[: wins + 1]) if low else sum(pmf[wins:])
    if tail >= 0.05:
        status, text = "normal", "normal"
    elif tail >= 0.01:
        status, text = "unusual", f"unusually {'poor' if low else 'good'}"
    else:
        status, text = "very_unusual", f"very unusually {'poor' if low else 'good'}"
    return {
        "status": status, "wins": wins, "trades": n, "expected_wins": round(expected, 1),
        "probability": round(tail, 4),
        "text": (f"{wins} win{'s' if wins != 1 else ''} in {n} trades: a result this "
                 f"{'poor' if low else 'good'} or worse happens {_share(tail)} of the time at the backtest's "
                 f"{win_rate_pct:.1f}% win rate, so it's {text}.").replace("this good or worse", "this good or better"),
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
        "targets_reset_reason": t.get("reset_reason"),
        "early_check": early_check(wins, n, t["win_rate_pct"]),
        "checks": [asdict(c) for c in checks],
    }
