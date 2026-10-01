"""
Backtest report.

Besides the usual figures, the report is built to expose what grid and basket
strategies hide best: many small wins paid for by rare large losses and by
long periods sitting on open losses.

  MAE  worst open P&L a basket went through (maximum adverse excursion)
  MFE  best open P&L it reached (maximum favourable excursion)
  largest_loss_in_avg_wins   how many average wins the worst basket erased
  winners_with_mae_over_3x_profit_pct   winners that sat through open losses
                                         more than 3x the profit they made

Sharpe uses daily equity returns (252 days, 365 for crypto). With weeks of
data it is noise; it is reported, not trusted. Buy-and-hold over the same
candles is shown so a profit that only reflects the market's drift is visible.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
import pandas as pd

from app.engine.robot import EquityPoint
from app.execution.order_intent import Fill
from app.market.instruments import Instrument
from app.strategy.basket_manager import CompletedBasket


def _pf(pnls: list[float]) -> float | None:
    gains = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    if losses == 0:
        return None if gains == 0 else math.inf
    return gains / losses


def clean(v):
    """JSON-safe: NaN -> None, inf -> "inf", numpy -> python, rounded floats."""
    if isinstance(v, (float, np.floating)):
        v = float(v)
        if math.isnan(v):
            return None
        if math.isinf(v):
            return "inf"
        return round(v, 6)
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def curve_frame(curve: list[EquityPoint]) -> pd.DataFrame:
    df = pd.DataFrame([p.__dict__ for p in curve])
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def _group(baskets: list[CompletedBasket], key: str) -> dict:
    groups: dict[str, list[CompletedBasket]] = {}
    for b in baskets:
        groups.setdefault(str(getattr(b, key)), []).append(b)
    out = {}
    for name, bs in sorted(groups.items()):
        pnls = [b.pnl for b in bs]
        out[name] = {"baskets": len(bs), "win_rate_pct": sum(p > 0 for p in pnls) / len(bs) * 100,
                     "net_pnl": sum(pnls), "avg_pnl": float(np.mean(pnls)), "profit_factor": _pf(pnls),
                     "worst_basket": min(pnls), "avg_mae": float(np.mean([b.mae for b in bs])),
                     "avg_positions": float(np.mean([b.positions for b in bs]))}
    return out


def compute_metrics(baskets: list[CompletedBasket], curve: list[EquityPoint], fills: list[Fill],
                    initial_capital: float, inst: Instrument, timeframe: str,
                    candles: pd.DataFrame | None = None) -> dict:
    cf = curve_frame(curve)
    pnls = [b.pnl for b in baskets]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(baskets)
    end_balance = initial_capital + sum(pnls)

    if not cf.empty:
        peak = cf["equity"].cummax()
        max_dd_pct = float(((cf["equity"] / peak - 1) * 100).min())
        max_dd_usd = float((cf["equity"] - peak).min())
        eq = cf["equity"].where(cf["equity"] > 0)
        lev = (cf["notional"] / eq).fillna(0)
        margin_use = (cf["margin"] / eq * 100).fillna(0)
        in_market = float((cf["positions"] > 0).mean() * 100)
        worst_floating = float(cf["floating"].min())
    else:
        max_dd_pct = max_dd_usd = worst_floating = 0.0
        lev = margin_use = pd.Series([0.0])
        in_market = 0.0

    streak = worst_streak = 0
    for p in pnls:
        streak = streak + 1 if p <= 0 else 0
        worst_streak = max(worst_streak, streak)

    sharpe = None
    if len(cf) > 2:
        daily = cf.set_index("time")["equity"].resample("1D").last().dropna()
        r = daily.pct_change().dropna()
        periods = 365 if inst.trades_24_7 else 252
        if len(r) > 5 and r.std() > 0:
            sharpe = float(r.mean() / r.std() * math.sqrt(periods))

    mae = [b.mae for b in baskets]
    win_b = [b for b in baskets if b.pnl > 0]
    ratios = [b.pnl / abs(b.mae) for b in win_b if b.mae < 0]
    mae_block = {
        "avg_mae": float(np.mean(mae)) if mae else None,
        "worst_mae": min(mae) if mae else None,
        "avg_mfe": float(np.mean([b.mfe for b in baskets])) if baskets else None,
        "avg_winner_mae": float(np.mean([b.mae for b in win_b])) if win_b else None,
        "median_winner_profit_to_mae": float(np.median(ratios)) if ratios else None,
        "winners_with_mae_over_1x_profit_pct": sum(abs(b.mae) > b.pnl for b in win_b) / len(win_b) * 100 if win_b else None,
        "winners_with_mae_over_3x_profit_pct": sum(abs(b.mae) > 3 * b.pnl for b in win_b) / len(win_b) * 100 if win_b else None,
        "worst_mae_in_avg_wins": abs(min(mae)) / np.mean(wins) if (mae and wins) else None,
        "largest_loss_in_avg_wins": abs(min(pnls)) / np.mean(wins) if (losses and wins and min(pnls) < 0) else None,
    }

    spread_cost = sum(f.spread / 2 * inst.usd_per_price_unit(f.lots, f.reference_mid) for f in fills)
    slip_cost = sum(f.slippage * inst.usd_per_price_unit(f.lots, f.reference_mid) for f in fills)
    commission = sum(f.commission for f in fills)

    bh = None
    if candles is not None and len(candles) > 1:
        p0, p1 = float(candles["open"].iloc[0]), float(candles["close"].iloc[-1])
        bh = {"price_change_pct": (p1 / p0 - 1) * 100, "first_open": p0, "last_close": p1}

    monthly = []
    if not cf.empty:
        m = cf.set_index("time")["equity"].resample("MS").last()
        start = initial_capital
        for ts, e in m.items():
            monthly.append({"month": ts.strftime("%Y-%m"), "pnl": e - start, "return_pct": (e / start - 1) * 100})
            start = e

    dist = []
    if pnls:
        counts, edges = np.histogram(pnls, bins=min(30, max(5, n // 5)))
        dist = [{"from": float(edges[k]), "to": float(edges[k + 1]), "count": int(counts[k])} for k in range(len(counts))]

    bars_held = [b.bars_held for b in baskets]
    out = {
        "symbol": inst.symbol, "timeframe": timeframe,
        "start": cf["time"].iloc[0].isoformat() if not cf.empty else None,
        "end": cf["time"].iloc[-1].isoformat() if not cf.empty else None,
        "bars": len(cf),
        "starting_balance": initial_capital, "ending_balance": end_balance, "net_pnl": end_balance - initial_capital,
        "return_pct": (end_balance / initial_capital - 1) * 100,
        "baskets": n, "winning_baskets": len(wins), "losing_baskets": len(losses),
        "win_rate_pct": len(wins) / n * 100 if n else None,
        "avg_basket_profit": float(np.mean(wins)) if wins else None,
        "avg_basket_loss": float(np.mean(losses)) if losses else None,
        "largest_basket_profit": max(pnls) if pnls else None,
        "largest_basket_loss": min(pnls) if pnls else None,
        "profit_factor": _pf(pnls), "expectancy_per_basket": float(np.mean(pnls)) if pnls else None,
        "max_drawdown_pct": max_dd_pct, "max_drawdown_usd": max_dd_usd,
        "worst_floating_pnl": worst_floating,
        "max_consecutive_losses": worst_streak,
        "avg_basket_bars": float(np.mean(bars_held)) if bars_held else None,
        "max_basket_bars": max(bars_held) if bars_held else None,
        "avg_basket_minutes": float(np.mean([b.duration_minutes for b in baskets])) if baskets else None,
        "max_positions_in_basket": max((b.positions for b in baskets), default=0),
        "avg_positions_per_basket": float(np.mean([b.positions for b in baskets])) if baskets else None,
        "max_lots": max((b.max_lots for b in baskets), default=0.0),
        "max_notional_usd": float(cf["notional"].max()) if not cf.empty else 0.0,
        "max_effective_leverage": float(lev.max()),
        "max_margin_usd": max((b.max_margin for b in baskets), default=0.0),
        "max_margin_usage_pct": float(margin_use.max()),
        "time_in_market_pct": in_market,
        "sharpe_daily": sharpe,
        "costs": {"spread_usd": spread_cost, "slippage_usd": slip_cost, "commission_usd": commission,
                  "total_usd": spread_cost + slip_cost + commission, "fills": len(fills)},
        "buy_and_hold": bh,
        "close_reasons": dict(Counter(b.close_reason for b in baskets)),
        "positions_histogram": {str(k): v for k, v in sorted(Counter(b.positions for b in baskets).items())},
        "mae_mfe": mae_block,
        "by_regime": _group(baskets, "regime"),
        "by_direction": _group(baskets, "direction"),
        "by_close_reason": _group(baskets, "close_reason"),
        "monthly": monthly,
        "pnl_distribution": dist,
    }
    return clean(out)


def downsample_curve(curve: list[EquityPoint], max_points: int = 1500) -> list[dict]:
    """Equity / drawdown / exposure curve for charts, thinned (always keeping the worst drawdown point)."""
    cf = curve_frame(curve)
    if cf.empty:
        return []
    peak = cf["equity"].cummax()
    cf["drawdown_pct"] = (cf["equity"] / peak - 1) * 100
    step = max(1, len(cf) // max_points)
    keep = set(range(0, len(cf), step)) | {int(cf["drawdown_pct"].idxmin()), len(cf) - 1}
    rows = cf.iloc[sorted(keep)]
    return [clean({"time": r.time.isoformat(), "equity": r.equity, "balance": r.balance, "floating": r.floating,
                   "drawdown_pct": r.drawdown_pct, "positions": int(r.positions), "notional": r.notional,
                   "price": r.price}) for r in rows.itertuples()]


def report(result, max_baskets: int = 500) -> dict:
    """Full JSON report for a BacktestResult."""
    r = result.robot
    s = result.settings
    m = compute_metrics(r.completed, r.equity_curve, r.ledger.fills, s.risk.initial_capital, r.inst,
                        s.market.timeframe, result.candles)
    return {
        "label": result.label, "preset": s.name, "strategy": r.strategy.name, "settings": s.model_dump(mode="json"),
        "high_risk_settings": s.high_risk, "assumptions": result.assumptions,
        "simulated": True, "metrics": m, "equity_curve": downsample_curve(r.equity_curve),
        "baskets": [clean(b.to_dict()) for b in r.completed[-max_baskets:]],
        "event_counts": dict(r.log.counts),
    }
