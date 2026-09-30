"""
Research studies behind the paper-trading setups, as reproducible code.

Two studies, both on the fixed baseline rules (no optimization):

- market_study: for each market, timeframe and strategy side (buy & sell,
  buy-only, sell-only), a full-period backtest plus period-by-period results
  (six-week windows on hourly data, calendar years on daily data), compared
  with simply holding the market.
- sensitivity_study: for each paper-account setup, one strategy setting at a
  time is moved away from its default and the full backtest rerun, to check
  the result doesn't hinge on lucky settings.

`python -m app.cli research` refreshes the local data cache, runs both, and
writes app/research/results.json, which the API serves at GET /research.
Studies read only from the cache (data/<symbol>_<timeframe>.parquet).
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.metrics import compute_metrics
from app.config import settings
from app.data.repository import load_processed
from app.data.validator import closed_candles
from app.features.feature_engineering import build_feature_matrix
from app.risk.risk_manager import RiskManager
from app.strategy.rules import baseline_signal
from app.utils.time import utc_now

RESULTS_PATH = Path(__file__).with_name("results.json")

MARKETS = [
    ("BTC-USD", "BTC/USD", "2020-01-01"),
    ("ETH-USD", "ETH/USD", "2020-01-01"),
    ("GC=F", "Gold", "2017-01-01"),
    ("USDJPY=X", "USD/JPY", "2017-01-01"),
    ("EURUSD=X", "EUR/USD", "2017-01-01"),
    ("GBPUSD=X", "GBP/USD", "2017-01-01"),
]
SIDES = {"both": "Buy & sell", "buy": "Buy-only", "sell": "Sell-only"}

# The ten live paper-account setups: (account, symbol, timeframe, side).
ACCOUNT_SETUPS = [
    ("btc_daily_long", "BTC-USD", "1d", "buy"),
    ("eth_daily_long", "ETH-USD", "1d", "buy"),
    ("btc_hourly_long", "BTC-USD", "1h", "buy"),
    ("eth_hourly_long", "ETH-USD", "1h", "buy"),
    ("btc_hourly_both", "BTC-USD", "1h", "both"),
    ("eth_hourly_both", "ETH-USD", "1h", "both"),
    ("gold_daily_long", "GC=F", "1d", "buy"),
    ("gold_hourly_long", "GC=F", "1h", "buy"),
    ("gold_hourly_both", "GC=F", "1h", "both"),
    ("usdjpy_hourly_long", "USDJPY=X", "1h", "buy"),
]
DEFAULT_PARAMS = dict(ema_fast=20, ema_slow=50, rsi=50.0, stop_atr_multiplier=2.0, take_profit_r=2.0)
PARAM_GRID = {
    "ema_fast": [10, 15, 25, 30],
    "ema_slow": [35, 40, 60, 75, 100],
    "rsi": [45.0, 55.0],
    "stop_atr_multiplier": [1.5, 2.5, 3.0],
    "take_profit_r": [1.5, 2.5, 3.0],
}
PARAM_LABELS = {
    "ema_fast": "Fast moving average",
    "ema_slow": "Slow moving average",
    "rsi": "RSI threshold",
    "stop_atr_multiplier": "Stop distance (× ATR)",
    "take_profit_r": "Profit target (× risk)",
}
HOURLY_WINDOW_WEEKS = 6
HOURLY_WINDOWS = 13


def _cfg(params: dict | None = None, max_drawdown_pct: float = 0.5):
    p = {**DEFAULT_PARAMS, **(params or {})}
    return settings.model_copy(update={
        "ema_fast": p["ema_fast"], "ema_slow": p["ema_slow"],
        "rsi_buy_threshold": p["rsi"], "rsi_sell_threshold": p["rsi"],
        "stop_atr_multiplier": p["stop_atr_multiplier"], "take_profit_r": p["take_profit_r"],
        "max_drawdown_pct": max_drawdown_pct,
    })


def _signals(candles: pd.DataFrame, timeframe: str, cfg, start: str | None) -> pd.DataFrame:
    f = build_feature_matrix(closed_candles(candles, timeframe), cfg)
    s = baseline_signal(f, cfg=cfg)
    f["sig_both"] = s
    f["sig_buy"] = s.where(s != "SELL", "HOLD")
    f["sig_sell"] = s.where(s != "BUY", "HOLD")
    f = f.dropna(subset=[f"ema_{cfg.ema_slow}", "ema_200", "atr"])
    if start:
        f = f[f["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    return f.reset_index(drop=True)


def _backtest(df: pd.DataFrame, symbol: str, timeframe: str, side: str, cfg) -> dict:
    result = BacktestEngine(BacktestConfig.from_settings(cfg, symbol, timeframe), risk_manager=RiskManager(cfg)).run(
        df, signal_col=f"sig_{side}")
    m = compute_metrics(result.portfolio, timeframe, symbol)
    pf = m.profit_factor if isinstance(m.profit_factor, float) and np.isfinite(m.profit_factor) else None
    return {
        "return_pct": round(m.total_return_pct, 2),
        "cagr_pct": None if m.cagr_pct is None else round(m.cagr_pct, 2),
        "max_drawdown_pct": round(m.max_drawdown_pct, 2),
        "sharpe": None if m.sharpe_ratio is None else round(m.sharpe_ratio, 2),
        "profit_factor": None if pf is None else round(pf, 3),
        "trades": m.total_trades,
        "win_rate_pct": round(m.win_rate_pct, 1),
    }


def _hold_pct(df: pd.DataFrame) -> float:
    return round(float(df["close"].iloc[-1] / df["open"].iloc[0] - 1) * 100, 2)


def _windows(df: pd.DataFrame, timeframe: str) -> list[tuple[pd.Timestamp, pd.Timestamp, str]]:
    ts = df["timestamp"]
    if timeframe == "1d":
        years = sorted(ts.dt.year.unique())
        return [(pd.Timestamp(f"{y}-01-01", tz="UTC"), pd.Timestamp(f"{y + 1}-01-01", tz="UTC"), str(y)) for y in years]
    step = pd.Timedelta(weeks=HOURLY_WINDOW_WEEKS)
    end = ts.iloc[-1].normalize()
    out = []
    while len(out) < HOURLY_WINDOWS and end - step >= ts.iloc[0]:
        out.append((end - step, end, f"{(end - step):%Y-%m-%d}"))
        end -= step
    return list(reversed(out))


def market_study(symbol: str, name: str, daily_start: str) -> list[dict]:
    rows = []
    for timeframe in ("1h", "1d"):
        cfg = _cfg()
        df = _signals(load_processed(symbol, timeframe), timeframe, cfg, daily_start if timeframe == "1d" else None)
        periods = []
        for a, b, label in _windows(df, timeframe):
            s = df[(df["timestamp"] >= a) & (df["timestamp"] < b)].reset_index(drop=True)
            if len(s) < 20:
                continue
            row = {"label": label, "hold_pct": _hold_pct(s)}
            for side in SIDES:
                row[side] = _backtest(s, symbol, timeframe, side, _cfg(max_drawdown_pct=settings.max_drawdown_pct))["return_pct"]
            periods.append(row)
        chained = lambda xs: round((float(np.prod([1 + x / 100 for x in xs])) - 1) * 100, 2)
        for side, side_label in SIDES.items():
            vals = [p[side] for p in periods]
            holds = [p["hold_pct"] for p in periods]
            rows.append({
                "symbol": symbol, "market": name, "timeframe": timeframe, "side": side, "side_label": side_label,
                "start": f"{df['timestamp'].iloc[0]:%Y-%m-%d}", "end": f"{df['timestamp'].iloc[-1]:%Y-%m-%d}",
                "hold_pct": _hold_pct(df),
                "full": _backtest(df, symbol, timeframe, side, cfg),
                "periods": {
                    "kind": "calendar year" if timeframe == "1d" else f"{HOURLY_WINDOW_WEEKS}-week window",
                    "count": len(vals),
                    "profitable": int(sum(v > 0 for v in vals)),
                    "avg_pct": round(float(np.mean(vals)), 2) if vals else None,
                    "worst_pct": round(min(vals), 2) if vals else None,
                    "chained_pct": chained(vals),
                    "hold_chained_pct": chained(holds),
                    "corr_with_hold": round(float(np.corrcoef(vals, holds)[0, 1]), 2) if len(vals) > 2 else None,
                    "rows": [{"label": p["label"], "hold_pct": p["hold_pct"], "return_pct": p[side]} for p in periods],
                },
            })
    return rows


def _sensitivity_job(job) -> dict:
    account, symbol, timeframe, side, start, label, params = job
    cfg = _cfg(params)
    df = _signals(load_processed(symbol, timeframe), timeframe, cfg, start)
    return {"account": account, "variant": label, **_backtest(df, symbol, timeframe, side, cfg)}


def sensitivity_study(workers: int = 4) -> list[dict]:
    starts = {m[0]: m[2] for m in MARKETS}
    jobs = []
    for account, symbol, timeframe, side in ACCOUNT_SETUPS:
        start = starts[symbol] if timeframe == "1d" else None
        jobs.append((account, symbol, timeframe, side, start, "default", {}))
        for param, values in PARAM_GRID.items():
            for v in values:
                jobs.append((account, symbol, timeframe, side, start, f"{param}={v}", {param: v}))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(_sensitivity_job, jobs, chunksize=2))
    out = []
    for account, symbol, timeframe, side in ACCOUNT_SETUPS:
        mine = [r for r in rows if r["account"] == account]
        default = next(r for r in mine if r["variant"] == "default")
        variants = [r for r in mine if r["variant"] != "default"]
        rets = [r["return_pct"] for r in variants]
        out.append({
            "account": account, "symbol": symbol, "timeframe": timeframe, "side": side,
            "default": default,
            "variants": [{**r, "param": r["variant"].split("=")[0],
                          "param_label": PARAM_LABELS[r["variant"].split("=")[0]],
                          "value": float(r["variant"].split("=")[1])} for r in variants],
            "profitable": int(sum(x > 0 for x in rets)),
            "count": len(rets),
            "min_pct": min(rets), "max_pct": max(rets), "median_pct": float(np.median(rets)),
            "default_rank": 1 + int(sum(r > default["return_pct"] for r in rets)),
        })
    return out


def run_all(workers: int = 4) -> dict:
    markets = []
    for symbol, name, start in MARKETS:
        markets.extend(market_study(symbol, name, start))
    return {
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "defaults": DEFAULT_PARAMS,
        "note": "Full-period backtests use a 50% drawdown limit so a halt doesn't cut them short; "
                "period results use the normal limit. Costs are each market's spread and slippage.",
        "markets": markets,
        "sensitivity": sensitivity_study(workers),
    }


def save(results: dict, path: Path = RESULTS_PATH) -> Path:
    path.write_text(json.dumps(results, indent=1))
    return path


def load_results(path: Path = RESULTS_PATH) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None
