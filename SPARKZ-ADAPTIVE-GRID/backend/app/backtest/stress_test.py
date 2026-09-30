"""
Stress tests: the point is to find how the strategy fails, not how it earns.

Scenarios (each a separate run with the same strategy settings):
  1 strong_uptrend          synthetic trend up, strong drift
  2 strong_downtrend        synthetic trend down
  3 sideways                synthetic mean-reverting range
  4 volatility_spike        real data with a 60-bar block of 5x ranges and a 10-ATR shock
  5 one_directional_move    real data with a 25-ATR one-way slide laid over 400 bars
  6 large_spread            real data, spread x3
  7 slippage                real data, slippage x5 and one bar of entry latency
  8 losing_streak           the baseline's own baskets reordered with every loss in a row
  9 repeated_reentries      synthetic slow grind against the entries, so grids fill again and again
 10 max_basket              what a full basket costs: exposure, margin and loss at max positions,
                            and how baskets that reached max positions actually ended

Synthetic series are scaled from the real data (price level and typical
bar size), with a fixed seed so results are reproducible.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.backtest.runner import prepare_features, run_backtest
from app.backtest.summary import pick
from app.config import Settings
from app.data.instruments import get_instrument
from app.data.repository import load_candles
from app.strategy.position_sizing import base_lot, planned_lots
from app.utils.time import bar_length


def _bar_scale(candles: pd.DataFrame) -> tuple[float, float]:
    r = np.log(candles["close"]).diff().abs().median()
    return float(candles["close"].iloc[-1]), float(r)


def synthetic(n: int, start: float, sigma: float, drift_sigmas: float = 0.0, mean_revert: float = 0.0,
              seed: int = 7, t0: str = "2025-01-06", freq: str = "15min") -> pd.DataFrame:
    """Log-price walk: per-bar noise sigma (as a fraction), drift in sigmas per bar, optional pull to start."""
    rng = np.random.default_rng(seed)
    logp = [np.log(start)]
    for _ in range(n - 1):
        pull = -mean_revert * (logp[-1] - np.log(start))
        logp.append(logp[-1] + drift_sigmas * sigma + pull + rng.normal(0, sigma * 1.25))
    close = np.exp(np.array(logp))
    open_ = np.concatenate([[start], close[:-1]])
    wick = np.abs(rng.normal(0, sigma * 0.6, n)) * close
    ts = pd.date_range(t0, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": np.maximum(open_, close) + wick,
                         "low": np.minimum(open_, close) - wick, "close": close, "volume": 0.0})


def overlay_ramp(candles: pd.DataFrame, start: int, bars: int, total_move: float) -> pd.DataFrame:
    """Add a steady one-way move (price units) from `start` for `bars` bars, and keep the offset afterwards."""
    df = candles.copy()
    ramp = np.zeros(len(df))
    ramp[start:start + bars] = np.linspace(0, total_move, bars)
    ramp[start + bars:] = total_move
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c] + ramp
    return df


def inject_spike(candles: pd.DataFrame, start: int, bars: int, range_mult: float, shock: float) -> pd.DataFrame:
    """Blow out bar ranges by `range_mult` for `bars` bars and gap price by `shock` (price units) at `start`."""
    df = overlay_ramp(candles, start, 1, shock)
    sl = slice(start, start + bars)
    mid = (df.loc[sl, "open"] + df.loc[sl, "close"]) / 2
    for c in ["open", "high", "low", "close"]:
        df.loc[sl, c] = mid + (df.loc[sl, c] - mid) * range_mult
    df.loc[sl, "high"] = df.loc[sl, ["open", "high", "close"]].max(axis=1)
    df.loc[sl, "low"] = df.loc[sl, ["open", "low", "close"]].min(axis=1)
    return df


def _worst_order_drawdown(pnls: list[float], capital: float) -> float:
    ordered = sorted(pnls)             # every loss first, worst first
    eq = capital + np.cumsum(ordered)
    peak = np.maximum.accumulate(np.concatenate([[capital], eq]))[1:]
    return float(((eq / peak) - 1).min() * 100) if len(eq) else 0.0


def run_stress(settings: Settings, candles: pd.DataFrame | None = None) -> dict:
    m = settings.market
    if candles is None:
        candles = load_candles(m.symbol, m.timeframe)
    freq = f"{int(bar_length(m.timeframe).total_seconds() // 60)}min"
    price, sigma = _bar_scale(candles)
    real_f = prepare_features(candles, settings)
    atr = float(real_f["atr"].median())
    n = max(3000, min(len(candles), 6000))
    mid = len(candles) // 2
    base = run_backtest(settings, features=real_f)

    def run(df=None, s=None, features=None):
        s = s or settings
        return run_backtest(s, features=features if features is not None else prepare_features(df, s))

    def with_exec(**kw):
        return settings.model_copy(update={"execution": settings.execution.model_copy(update=kw)})

    scenarios = [
        ("strong_uptrend", "Synthetic, drift +0.25 sigma per bar", lambda: run(synthetic(n, price, sigma, 0.25, seed=11, freq=freq))),
        ("strong_downtrend", "Synthetic, drift -0.25 sigma per bar", lambda: run(synthetic(n, price, sigma, -0.25, seed=12, freq=freq))),
        ("sideways", "Synthetic mean-reverting range", lambda: run(synthetic(n, price, sigma, 0.0, mean_revert=0.05, seed=13, freq=freq))),
        ("volatility_spike", "Real data, 60 bars at 5x range and a -10 ATR shock mid-sample",
         lambda: run(inject_spike(candles, mid, 60, 5.0, -10 * atr))),
        ("one_directional_move", "Real data with a -25 ATR slide over 400 bars mid-sample",
         lambda: run(overlay_ramp(candles, mid, 400, -25 * atr))),
        ("large_spread", "Real data, spread x3", lambda: run(s=with_exec(spread_multiplier=3 * settings.execution.spread_multiplier), features=real_f)),
        ("slippage", "Real data, slippage x5 and 1 bar entry latency",
         lambda: run(s=with_exec(slippage_multiplier=5 * settings.execution.slippage_multiplier, entry_latency_bars=1), features=real_f)),
        ("repeated_reentries", "Synthetic slow grind (drift -0.08 sigma, low noise) that keeps filling grids",
         lambda: run(synthetic(n, price, sigma * 0.6, -0.08, seed=14, freq=freq))),
    ]
    rows = [{"scenario": "baseline", "description": "Real data, configured costs", **pick(base.metrics)}]
    for key, desc, fn in scenarios:
        r = fn()
        rows.append({"scenario": key, "description": desc, **pick(r.metrics),
                     "worst_basket_reason": min(r.baskets, key=lambda b: b.pnl).close_reason if r.baskets else None})

    pnls = [b.pnl for b in base.baskets]
    losses = [p for p in pnls if p <= 0]
    rows.append({"scenario": "losing_streak", "description": f"Baseline baskets reordered: all {len(losses)} losses in a row",
                 "max_drawdown_pct": _worst_order_drawdown(pnls, settings.risk.initial_capital),
                 "max_consecutive_losses": len(losses), "net_pnl": sum(pnls), "baskets": len(pnls),
                 "halted": _worst_order_drawdown(pnls, settings.risk.initial_capital) <= -settings.risk.max_account_drawdown_percent or None})

    inst = get_instrument(m.symbol)
    lots = planned_lots(settings.risk.max_positions, settings.sizing, inst, base_lot(settings.sizing, inst, atr, price))
    total = sum(lots)
    full = [b for b in base.baskets if b.positions >= settings.risk.max_positions]
    max_basket = {
        "scenario": "max_basket", "description": f"A basket at max positions ({settings.risk.max_positions})",
        "planned_lots": lots, "total_lots": round(total, 4),
        "notional_usd": inst.notional_usd(total, price), "margin_usd": inst.margin_usd(total, price),
        "leverage_at_start_equity": inst.notional_usd(total, price) / settings.risk.initial_capital,
        "usd_per_1atr_move": inst.usd_per_price_unit(total, price) * atr,
        "loss_limit_usd": settings.risk.initial_capital * min(settings.stop.max_basket_loss_percent, settings.risk.risk_per_cycle * 100) / 100,
        "baskets_reaching_max": len(full), "their_win_rate_pct": (sum(b.pnl > 0 for b in full) / len(full) * 100) if full else None,
        "their_net_pnl": sum(b.pnl for b in full) if full else 0.0,
        "their_avg_pnl": float(np.mean([b.pnl for b in full])) if full else None,
    }
    return {"symbol": m.symbol, "timeframe": m.timeframe, "strategy": settings.name, "median_atr": atr,
            "rows": rows, "max_basket": max_basket}
