"""
Stress tests: the strategy in situations it is built to suffer in.

1. Market scenarios (synthetic, several seeds each): normal, strong trends,
   sideways, high volatility, a sudden move, rapid reversals, news spikes and
   a relentless one-way move. For each: P&L, worst basket, drawdown, how often
   baskets filled the grid to max positions, loss-limit closes, losing streaks.
2. Execution stress on one series (real or synthetic): spread x3, slippage x5,
   both, and order latency.
3. The CRITICAL FAILURE SCENARIO: a basket is open, then the market moves hard
   against it and keeps going. Bar by bar it records floating loss, exposure,
   margin, positions, distance to break-even (and to the target), MAE and the
   account drawdown, for:
     - the preset as configured (basket loss limit closes the basket);
     - the same with close_basket_on_limit off (what a grid without a stop does);
     - LINEAR and MULTIPLIER sizing (exposure growth).
   The point is to SHOW the loss, not to make it look small.

Synthetic data is labelled as such in every result.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.backtest.engine import run_backtest
from app.backtest.metrics import clean, compute_metrics
from app.config import TIMEFRAMES, Settings, deep_merge
from app.market.providers.mock_provider import DAILY_VOL, generate_candles

MARKET_SCENARIOS = {
    "normal": "random walk, no drift",
    "trend_up": "strong steady uptrend",
    "trend_down": "strong steady downtrend",
    "sideways": "mean-reverting range",
    "high_vol": "3x normal volatility",
    "spike": "one sudden 8-ATR move mid-series",
    "reversals": "rapid trend reversals every 120 bars",
    "news_spikes": "random 5-12x volatility shocks (~1 in 150 bars)",
    "adverse_trend": "relentless one-way move up (repeated adverse movement for SELL baskets)",
}

EXECUTION_STRESS = {
    "base": {},
    "spread_x3": {"execution": {"spread_multiplier": 3.0}},
    "slippage_x5": {"execution": {"slippage_multiplier": 5.0}},
    "spread_x3_slippage_x5": {"execution": {"spread_multiplier": 3.0, "slippage_multiplier": 5.0}},
    "latency_60s": {"execution": {"delay_ms": 60_000}},
}


def _with(settings: Settings, overrides: dict) -> Settings:
    return Settings.model_validate(deep_merge(settings.model_dump(mode="json"), overrides))


def _summary(result) -> dict:
    r = result.robot
    m = compute_metrics(r.completed, r.equity_curve, r.ledger.fills, result.settings.risk.initial_capital, r.inst,
                        result.settings.market.timeframe, result.candles)
    maxp = result.settings.risk.max_positions
    return {k: m[k] for k in ("net_pnl", "return_pct", "baskets", "win_rate_pct", "profit_factor",
                              "largest_basket_loss", "max_drawdown_pct", "worst_floating_pnl",
                              "max_consecutive_losses", "max_positions_in_basket", "max_effective_leverage",
                              "max_margin_usage_pct", "close_reasons")} | {
        "baskets_at_max_positions": sum(1 for b in r.completed if b.positions >= maxp),
        "loss_limit_closes": sum(1 for b in r.completed if b.close_reason == "LOSS_LIMIT"),
        "halted": r.halted, "buy_and_hold_pct": (m["buy_and_hold"] or {}).get("price_change_pct"),
        "costs_usd": m["costs"]["total_usd"],
    }


def market_scenarios(settings: Settings, bars: int = 3000, seeds: tuple[int, ...] = (1, 2, 3)) -> dict:
    sym, tf = settings.market.symbol, settings.market.timeframe
    out = {}
    for name, desc in MARKET_SCENARIOS.items():
        runs = [_summary(run_backtest(settings, generate_candles(sym, tf, bars, name, seed=s), f"{name}#{s}"))
                for s in seeds]
        pnl = [x["net_pnl"] for x in runs]
        out[name] = {"description": desc, "synthetic": True, "seeds": list(seeds), "bars": bars,
                     "mean_net_pnl": float(np.mean(pnl)), "worst_net_pnl": min(pnl), "best_net_pnl": max(pnl),
                     "worst_drawdown_pct": min(x["max_drawdown_pct"] for x in runs),
                     "worst_basket": min((x["largest_basket_loss"] or 0) for x in runs),
                     "baskets_at_max_positions": sum(x["baskets_at_max_positions"] for x in runs),
                     "loss_limit_closes": sum(x["loss_limit_closes"] for x in runs),
                     "max_consecutive_losses": max(x["max_consecutive_losses"] for x in runs),
                     "halted_runs": sum(1 for x in runs if x["halted"]), "runs": runs}
    return clean(out)


def execution_stress(settings: Settings, candles: pd.DataFrame, label: str = "") -> dict:
    out = {}
    for name, ov in EXECUTION_STRESS.items():
        out[name] = _summary(run_backtest(_with(settings, ov), candles, f"{label}:{name}"))
    return clean({"label": label, "variants": out})


# ---------------------------------------------------------------------------- critical failure
def _adverse_candles(settings: Settings, seed: int = 5, warm_bars: int = 700, drop_bars: int = 80,
                     drift_vol: float = 0.7) -> tuple[pd.DataFrame, int, str]:
    """An uptrend until the strategy holds a basket, then a relentless move against that basket."""
    sym, tf = settings.market.symbol, settings.market.timeframe
    base = generate_candles(sym, tf, warm_bars, "trend_up", seed=seed)
    found: dict = {}

    def watch(i, robot):
        if "i" not in found and i >= 450 and robot.strategy.basket is not None:
            found["i"], found["dir"] = i, robot.strategy.basket.direction
    run_backtest(settings, base, on_bar=watch)
    if "i" not in found:
        raise RuntimeError("no basket opened in the warm-up series")
    k, direction = found["i"], found["dir"]
    vol = DAILY_VOL.get(sym, 0.01) * np.sqrt(TIMEFRAMES[tf] / 86_400)
    rng = np.random.default_rng(seed + 1000)
    sign = -1 if direction == "BUY" else 1
    r = sign * drift_vol * vol + 0.4 * vol * rng.standard_normal(drop_bars)
    p0 = float(base["close"].iloc[k])
    close = p0 * np.exp(np.cumsum(r))
    open_ = np.concatenate([[p0], close[:-1]])
    wick = np.abs(rng.normal(0, 0.3 * vol, (drop_bars, 2))) * close[:, None]
    step = TIMEFRAMES[tf]
    ts = pd.date_range(base["timestamp"].iloc[k] + pd.Timedelta(seconds=step), periods=drop_bars,
                       freq=f"{step}s", tz="UTC")
    drop = pd.DataFrame({"timestamp": ts, "open": open_, "high": np.maximum(open_, close) + wick[:, 0],
                         "low": np.minimum(open_, close) - wick[:, 1], "close": close, "volume": 500.0})
    return pd.concat([base.iloc[:k + 1], drop], ignore_index=True), k, direction


def _trajectory(settings: Settings, candles: pd.DataFrame, k: int) -> dict:
    rows: list[dict] = []
    target_uid: dict = {}

    def rec(i, robot):
        b = robot.strategy.basket
        if i == k and b is not None:
            target_uid["uid"] = b.uid
        if i < k or "uid" not in target_uid:
            return
        lt = robot.market.last_bar
        a = robot.account
        row = {"bar": i - k, "time": lt.timestamp.isoformat(), "price": lt.close, "atr": lt.atr,
               "account_equity": a.equity, "account_drawdown_pct": a.dd.current_pct(a.equity)}
        if b is not None and b.uid == target_uid["uid"]:
            fe = robot.strategy.fe
            exit_px = fe.close_price(b.direction, lt.close, lt.spread)
            rcv = b.recovery(exit_px, lt.close, lt.atr, robot.inst, fe.half_cost(lt.spread))
            row |= {"open": True, "positions": b.n, "lots": b.total_lots, "average_entry": b.avg_entry,
                    "floating_pnl": a.floating_pnl, "exposure_usd": robot.inst.notional_usd(b.total_lots, lt.close),
                    "margin_usd": robot.inst.margin_usd(b.total_lots, lt.close),
                    "margin_usage_pct": robot.inst.margin_usd(b.total_lots, lt.close) / a.equity * 100,
                    "mae": b.mae, "to_break_even": rcv["break_even"]["move"],
                    "to_break_even_atr": rcv["break_even"]["move_atr"], "to_target": rcv["target"]["move"],
                    "to_loss_limit": rcv["loss_limit"]["move"], "adds_blocked": b.adds_blocked}
        else:
            row |= {"open": False}
        rows.append(row)

    res = run_backtest(settings, candles, "critical_failure", on_bar=rec)
    done = next((b for b in res.robot.completed if b.uid == target_uid.get("uid")), None)
    open_rows = [x for x in rows if x.get("open")]
    return {
        "basket": done.to_dict() if done else None,
        "outcome": done.close_reason if done else "STILL_OPEN",
        "realized_pnl": done.pnl if done else None,
        "worst_floating_pnl": min((x["floating_pnl"] for x in open_rows), default=None),
        "max_positions": max((x["positions"] for x in open_rows), default=None),
        "max_exposure_usd": max((x["exposure_usd"] for x in open_rows), default=None),
        "max_margin_usage_pct": max((x["margin_usage_pct"] for x in open_rows), default=None),
        "max_distance_to_break_even_atr": max((x["to_break_even_atr"] or 0 for x in open_rows), default=None),
        "worst_account_drawdown_pct": max((x["account_drawdown_pct"] for x in rows), default=None),
        "halted": res.robot.halted,
        "bars_open": len(open_rows),
        "path": rows[:120],
    }


def critical_failure(settings: Settings, seed: int = 5) -> dict:
    candles, k, direction = _adverse_candles(settings, seed)
    move = (candles["close"].iloc[-1] / candles["close"].iloc[k] - 1) * 100
    variants = {
        "as_configured": {},
        "no_close_at_loss_limit": {"risk": {"close_basket_on_limit": False}},
        "linear_sizing": {"sizing": {"mode": "LINEAR"}},
        "multiplier_sizing_HIGH_RISK": {"sizing": {"mode": "MULTIPLIER", "multiplier": 2.0,
                                                    "allow_multiplier_sizing": True}},
    }
    out = {"synthetic": True, "basket_direction": direction, "adverse_move_pct": move,
           "adverse_bars": len(candles) - k - 1,
           "description": f"A {direction} basket is open when price starts moving {abs(move):.1f}% against it "
                          f"over {len(candles) - k - 1} bars without a meaningful bounce.",
           "variants": {}}
    for name, ov in variants.items():
        out["variants"][name] = _trajectory(_with(settings, ov), candles, k)
    return clean(out)


def run_all(settings: Settings, candles: pd.DataFrame | None = None, label: str = "", bars: int = 3000,
            seeds: tuple[int, ...] = (1, 2, 3)) -> dict:
    out = {"preset": settings.name, "market_scenarios": market_scenarios(settings, bars, seeds),
           "critical_failure": critical_failure(settings)}
    base = candles if candles is not None else generate_candles(settings.market.symbol, settings.market.timeframe,
                                                                bars, "normal", seed=1)
    out["execution_stress"] = execution_stress(settings, base, label or "synthetic normal")
    out["summary"] = {"scenarios_losing_money": [k for k, v in out["market_scenarios"].items()
                                                 if v["mean_net_pnl"] < 0]}
    return out
