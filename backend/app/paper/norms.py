"""
Normal-losses check: is a paper account's bad patch something its own backtest
already lived through, or something new?

The 30-trade verdict (app.paper.evaluation) takes weeks. Until then a run of
losses is hard to read: an hourly strategy that wins ~45% of the time loses
three in a row routinely, yet it feels like breakage. So each account also
gets a record of the worst stretches its backtest went through, and the live
account is compared against them:

- losing streak: longest run of consecutive losing trades;
- daily loss limit: how often a UTC day's realized losses reached the
  max_daily_loss_pct limit, and the most such days inside any 7 days;
- worst week: the worst 7-day change in (realized) balance.

A live figure worse than anything in the backtest is flagged "outside the
backtest's range". That's an early warning to look closer, not a verdict: it
never changes the pass/fail targets, and those stay fixed as before.

The norms come from the same backtest window as the account's targets, so
both describe the same history. They're reference figures only, so unlike
the targets they may be added to an existing account after the fact.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from app.config import Settings, settings
from app.utils.time import utc_now

WEEK = timedelta(days=7)


def _losing_runs(pnls: list[float]) -> list[int]:
    runs, n = [], 0
    for p in pnls:
        if p <= 0:
            n += 1
        else:
            if n:
                runs.append(n)
            n = 0
    if n:
        runs.append(n)
    return runs


def _limit_days(closes: list[tuple[datetime, float]], start_balance: float, limit_pct: float) -> list[pd.Timestamp]:
    """UTC days whose realized losses reached `limit_pct` of the balance at the start of that day."""
    if not closes:
        return []
    df = pd.DataFrame(closes, columns=["t", "pnl"])
    df["day"] = pd.to_datetime(df["t"], utc=True).dt.floor("D")
    df["before"] = start_balance + df["pnl"].cumsum() - df["pnl"]
    days = []
    for day, g in df.groupby("day", sort=True):
        start = g["before"].iloc[0]
        # The risk manager counts losses only, so a later win the same day doesn't undo a breach.
        running = g["pnl"].clip(upper=0).cumsum()
        if start > 0 and (-running.min()) / start >= limit_pct:
            days.append(day)
    return days


def _max_in_week(days: list[pd.Timestamp]) -> int:
    best = 0
    for i, d in enumerate(days):
        best = max(best, sum(1 for x in days[i:] if x < d + WEEK))
    return best


def _worst_week_pct(balance: pd.Series) -> float:
    """Worst change over any 7 days, from a balance series indexed by UTC time: each point is
    compared with the balance 7 days before it (or with the start, for the first week)."""
    if balance.empty:
        return 0.0
    balance = balance.sort_index()
    base = balance.reindex(balance.index - WEEK, method="ffill").to_numpy()
    base = pd.Series(base, index=balance.index).fillna(balance.iloc[0])
    return float(min(((balance / base - 1) * 100).min(), 0.0))


def _realized_balance(start: float, closes: list[tuple[datetime, float]]) -> pd.Series:
    times = pd.to_datetime([t for t, _ in closes], utc=True)
    values = start + pd.Series([p for _, p in closes]).cumsum().to_numpy()
    series = pd.Series(values, index=times)
    return series[~series.index.duplicated(keep="last")]


def norms_from_backtest(result, candle_times: pd.Series, cfg: Settings | None = None) -> dict:
    cfg = cfg or settings
    trades = sorted(result.portfolio.closed_trades, key=lambda t: t.exit_time)
    pnls = [t.pnl for t in trades]
    runs = _losing_runs(pnls)
    start = result.portfolio.initial_capital
    limit_days = _limit_days([(t.exit_time, t.pnl) for t in trades], start, cfg.max_daily_loss_pct)
    # Realized balance after each closed trade, so it compares like with like with the live
    # figure (which only knows closed trades).
    realized = _realized_balance(start, [(trades[0].entry_time, 0.0)] + [(t.exit_time, t.pnl) for t in trades]) \
        if trades else pd.Series(dtype=float)
    span_days = max(1, (pd.Timestamp(candle_times.iloc[-1]) - pd.Timestamp(candle_times.iloc[0])).days + 1)
    return {
        "set_at": utc_now().isoformat(timespec="seconds"),
        "days": span_days,
        "trades": len(trades),
        "max_losing_streak": max(runs, default=0),
        # {streak length: how many times it happened}; lets the page say how common the live streak is.
        "losing_streaks": {str(k): runs.count(k) for k in sorted(set(runs))},
        "daily_limit_pct": cfg.max_daily_loss_pct * 100,
        "daily_limit_days": len(limit_days),
        "max_daily_limit_days_in_week": _max_in_week(limit_days),
        "worst_week_pct": round(_worst_week_pct(realized), 2),
    }


def _streak_note(norms: dict, k: int) -> str:
    if k < 2:
        return ""
    times = sum(v for length, v in norms.get("losing_streaks", {}).items() if int(length) >= k)
    if not times:
        return f"the backtest never had {k} losses in a row"
    every = norms["days"] / times
    return f"backtest had {times} runs of {k}+ losses, about one every {every:.0f} days" if every >= 1.5 \
        else f"backtest had {times} runs of {k}+ losses, more than one a day"


def norms_status(state) -> dict | None:
    """Compare a PaperRunState's closed trades with its saved backtest norms."""
    n = state.norms
    if not n:
        return None
    trades = sorted(state.account.trade_history, key=lambda t: t.closed_at)
    pnls = [t.pnl for t in trades]
    runs = _losing_runs(pnls)
    current = 0
    for p in reversed(pnls):
        if p > 0:
            break
        current += 1
    longest = max(runs, default=0)

    start = state.config.starting_balance
    limit_days = _limit_days([(t.closed_at, t.pnl) for t in trades], start, n["daily_limit_pct"] / 100)
    week_days = _max_in_week(limit_days)

    worst_week = _worst_week_pct(_realized_balance(
        start, [(trades[0].opened_at, 0.0)] + [(t.closed_at, t.pnl) for t in trades])) if trades else 0.0

    def status(live, worst, lower_is_worse=False):
        beyond = live < worst if lower_is_worse else live > worst
        return "outside" if beyond else "normal"

    every = n["days"] / n["daily_limit_days"] if n["daily_limit_days"] else None
    items = [
        {
            "name": "Losing streak",
            "live": f"{current} now, longest {longest}",
            "backtest": f"longest {n['max_losing_streak']}",
            "note": _streak_note(n, current),
            "status": status(longest, n["max_losing_streak"]),
        },
        {
            "name": "Daily loss limit",
            "live": f"hit on {len(limit_days)} day{'s' if len(limit_days) != 1 else ''}, most in a week {week_days}",
            "backtest": f"most in a week {n['max_daily_limit_days_in_week']}",
            "note": (f"backtest hit the {n['daily_limit_pct']:.0f}% limit on {n['daily_limit_days']} of "
                     f"{n['days']} days, about once every {every:.0f} days") if every
                    else f"backtest never hit the {n['daily_limit_pct']:.0f}% limit",
            "status": status(week_days, n["max_daily_limit_days_in_week"]),
        },
        {
            "name": "Worst week",
            "live": f"{worst_week:.1f}% (closed trades)",
            "backtest": f"{n['worst_week_pct']:.1f}%",
            "note": "",
            "status": status(worst_week, n["worst_week_pct"], lower_is_worse=True),
        },
    ]
    outside = [i["name"] for i in items if i["status"] == "outside"]
    return {
        "verdict": ("Outside backtest range: " + ", ".join(outside)) if outside else "Within backtest range",
        "items": items,
        "backtest_days": n["days"],
        "backtest_trades": n["trades"],
    }
