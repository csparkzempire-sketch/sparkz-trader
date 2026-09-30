"""
Performance analytics for a finished run.

Beyond the usual figures, this answers the question grid and basket
strategies hide best: is the strategy collecting small profits while
sitting through large open losses? For every basket:

  MAE  maximum adverse excursion: the worst open (unrealized) P&L it went through
  MFE  maximum favourable excursion: the best open P&L it reached

and summaries such as "winning baskets whose MAE was more than 3x their final
profit". A strategy with a high win rate whose winners routinely sat through
losses many times their profit is taking tail risk the win rate doesn't show.

Sharpe and Sortino use daily returns of the equity curve, annualised with
252 trading days (365 for crypto). With only weeks of 15-minute data these
ratios are very noisy; they are reported, not trusted.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
import pandas as pd

from app.backtest.portfolio import EquityPoint
from app.strategy.basket_manager import CompletedBasketRecord


def _pf(pnls: list[float]) -> float | None:
    gains = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    if losses == 0:
        return None if gains == 0 else math.inf
    return gains / losses


def _clean(v):
    if isinstance(v, (float, np.floating)):
        v = float(v)
        if math.isnan(v):
            return None
        if math.isinf(v):
            return "inf"
        return round(v, 6)
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def curve_frame(curve: list[EquityPoint]) -> pd.DataFrame:
    df = pd.DataFrame([p.__dict__ for p in curve])
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def _group_stats(baskets: list[CompletedBasketRecord], key: str) -> dict:
    out = {}
    groups: dict[str, list[CompletedBasketRecord]] = {}
    for b in baskets:
        groups.setdefault(getattr(b, key), []).append(b)
    for name, bs in sorted(groups.items()):
        pnls = [b.pnl for b in bs]
        out[name] = {"baskets": len(bs), "win_rate_pct": sum(p > 0 for p in pnls) / len(bs) * 100,
                     "net_pnl": sum(pnls), "avg_pnl": float(np.mean(pnls)), "profit_factor": _pf(pnls),
                     "worst_basket": min(pnls), "avg_mae": float(np.mean([b.mae for b in bs])),
                     "avg_positions": float(np.mean([b.positions for b in bs]))}
    return out


def compute_metrics(baskets: list[CompletedBasketRecord], curve: list[EquityPoint], initial_capital: float,
                    symbol: str, timeframe: str) -> dict:
    cf = curve_frame(curve)
    end_balance = baskets and (initial_capital + sum(b.pnl for b in baskets)) or initial_capital
    if not cf.empty:
        end_balance = float(cf["balance"].iloc[-1])
    pnls = [b.pnl for b in baskets]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(baskets)

    # drawdown from the equity curve (bar closes, open P&L included)
    if not cf.empty:
        peak = cf["equity"].cummax()
        dd_pct = (cf["equity"] / peak - 1) * 100
        dd_usd = cf["equity"] - peak
        max_dd_pct, max_dd_usd = float(dd_pct.min()), float(dd_usd.min())
        lev = (cf["notional"] / cf["equity"].where(cf["equity"] > 0)).fillna(0)
        margin_use = (cf["margin"] / cf["equity"].where(cf["equity"] > 0) * 100).fillna(0)
        in_market = float((cf["positions"] > 0).mean() * 100)
    else:
        max_dd_pct = max_dd_usd = 0.0
        lev = margin_use = pd.Series([0.0])
        in_market = 0.0

    # longest losing streak
    streak = worst = 0
    for p in pnls:
        streak = streak + 1 if p <= 0 else 0
        worst = max(worst, streak)

    # Sharpe / Sortino from daily equity returns
    sharpe = sortino = None
    if len(cf) > 2:
        daily = cf.set_index("ts")["equity"].resample("1D").last().dropna()
        r = daily.pct_change().dropna()
        r = r[r != 0] if (r == 0).mean() > 0.5 else r   # weekends/idle days would deflate the variance
        periods = 365 if symbol.upper().startswith("BTC") else 252
        if len(r) > 5 and r.std() > 0:
            sharpe = float(r.mean() / r.std() * math.sqrt(periods))
            downside = r[r < 0]
            if len(downside) > 1 and downside.std() > 0:
                sortino = float(r.mean() / downside.std() * math.sqrt(periods))

    # MAE / MFE: profit per basket versus the open loss it took to get it
    mae = [b.mae for b in baskets]
    mfe = [b.mfe for b in baskets]
    win_b = [b for b in baskets if b.pnl > 0]
    ratios = [b.pnl / abs(b.mae) for b in win_b if b.mae < 0]
    mae_block = {
        "avg_mae": float(np.mean(mae)) if mae else None,
        "worst_mae": min(mae) if mae else None,
        "avg_mfe": float(np.mean(mfe)) if mfe else None,
        "avg_winner_profit": float(np.mean(wins)) if wins else None,
        "avg_winner_mae": float(np.mean([b.mae for b in win_b])) if win_b else None,
        "median_winner_profit_to_mae": float(np.median(ratios)) if ratios else None,
        "winners_with_mae_over_1x_profit_pct": (sum(1 for b in win_b if abs(b.mae) > b.pnl) / len(win_b) * 100) if win_b else None,
        "winners_with_mae_over_3x_profit_pct": (sum(1 for b in win_b if abs(b.mae) > 3 * b.pnl) / len(win_b) * 100) if win_b else None,
        "worst_mae_in_avg_wins": (abs(min(mae)) / np.mean(wins)) if (mae and wins) else None,
        "largest_loss_in_avg_wins": (abs(min(pnls)) / np.mean(wins)) if (losses and wins) else None,
        "scatter": [{"uid": b.uid, "pnl": b.pnl, "mae": b.mae, "mfe": b.mfe, "positions": b.positions} for b in baskets],
    }

    # monthly performance
    monthly = []
    if not cf.empty:
        m = cf.set_index("ts")["equity"].resample("MS").last()
        start = initial_capital
        for ts, eq in m.items():
            monthly.append({"month": ts.strftime("%Y-%m"), "pnl": eq - start, "return_pct": (eq / start - 1) * 100})
            start = eq

    # basket P&L distribution
    dist = []
    if pnls:
        counts, edges = np.histogram(pnls, bins=min(30, max(5, n // 5)))
        dist = [{"from": float(edges[k]), "to": float(edges[k + 1]), "count": int(counts[k])} for k in range(len(counts))]

    durations = [b.bars_held for b in baskets]
    out = {
        "symbol": symbol, "timeframe": timeframe,
        "start": cf["ts"].iloc[0].isoformat() if not cf.empty else None,
        "end": cf["ts"].iloc[-1].isoformat() if not cf.empty else None,
        "bars": len(cf),
        "starting_balance": initial_capital, "ending_balance": end_balance, "net_pnl": end_balance - initial_capital,
        "return_pct": (end_balance / initial_capital - 1) * 100,
        "baskets": n, "winning_baskets": len(wins), "losing_baskets": len(losses),
        "win_rate_pct": len(wins) / n * 100 if n else None,
        "avg_basket_profit": float(np.mean(wins)) if wins else None,
        "avg_basket_loss": float(np.mean(losses)) if losses else None,
        "largest_basket_profit": max(pnls) if pnls else None,
        "largest_basket_loss": min(pnls) if pnls else None,
        "profit_factor": _pf(pnls), "expectancy": float(np.mean(pnls)) if pnls else None,
        "max_drawdown_pct": max_dd_pct, "max_drawdown_usd": max_dd_usd,
        "max_consecutive_losses": worst,
        "avg_basket_bars": float(np.mean(durations)) if durations else None,
        "max_basket_bars": max(durations) if durations else None,
        "max_positions_in_basket": max((b.positions for b in baskets), default=0),
        "avg_positions_per_basket": float(np.mean([b.positions for b in baskets])) if baskets else None,
        "max_notional": float(cf["notional"].max()) if not cf.empty else 0.0,
        "max_effective_leverage": float(lev.max()),
        "max_margin_usage_pct": float(margin_use.max()),
        "time_in_market_pct": in_market,
        "sharpe": sharpe, "sortino": sortino,
        "close_reasons": dict(Counter(b.close_reason for b in baskets)),
        "positions_histogram": dict(sorted(Counter(b.positions for b in baskets).items())),
        "mae_mfe": mae_block,
        "by_regime": _group_stats(baskets, "regime"),
        "by_vol_regime": _group_stats(baskets, "vol_regime"),
        "monthly": monthly,
        "pnl_distribution": dist,
    }
    return _clean(out)


def downsample_curve(curve: list[EquityPoint], max_points: int = 1500) -> list[dict]:
    """Equity and drawdown curve for charts, thinned to at most `max_points` (keeps the worst drawdown point)."""
    cf = curve_frame(curve)
    if cf.empty:
        return []
    peak = cf["equity"].cummax()
    cf["drawdown_pct"] = (cf["equity"] / peak - 1) * 100
    step = max(1, len(cf) // max_points)
    idx = set(range(0, len(cf), step)) | {len(cf) - 1, int(cf["drawdown_pct"].idxmin())}
    out = cf.iloc[sorted(idx)]
    return [{"t": r.ts.isoformat(), "equity": round(r.equity, 2), "balance": round(r.balance, 2),
             "drawdown_pct": round(r.drawdown_pct, 3), "positions": int(r.positions)} for r in out.itertuples()]
