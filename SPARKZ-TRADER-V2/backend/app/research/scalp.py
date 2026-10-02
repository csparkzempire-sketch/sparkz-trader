"""
Structure scalping setup on 1m data: higher-timeframe bias, lower-timeframe change of character, fixed-R exits.

Rules (long; short is the mirror image):
  1. Bias      the last CLOSED higher-timeframe candle (default 15m) has a bullish structure trend.
  2. Session   the signal candle closes inside London / New York trading hours (default 07:00-20:00 UTC).
  3. Trigger   on the setup timeframe (1m or 5m) a CHOCH_UP: the lower timeframe had turned bearish (a
               pullback) and a close now breaks back above its last swing high.
               variant "sweep_choch" also requires a liquidity sweep of a low (wick below the last swing
               low or the Asia low, close back above) within the last `lookback` setup candles.
  4. Entry     at the open of the next 1m candle, at the ask (+ slippage).
  5. Stop      below the lowest low of the last `lookback` setup candles, minus `buffer`.
               Skipped if the risk is under `min_risk_spreads` x spread (costs would dominate) or over
               `max_risk`.
  6. Target    entry + rr x risk. Exits are checked on every 1m candle at the bid; when the stop and the
               target both fall inside one candle the STOP is assumed (the order is unknowable). A time
               stop closes the trade at market after `max_hold_min` minutes.
One trade at a time. Each trade risks `risk_usd`, so P&L in USD is R x risk_usd. Simulation only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from app.research.structure import asia_range, structure, sweeps

TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}


@dataclass(frozen=True)
class ScalpParams:
    tf: str = "1m"                  # setup timeframe
    swing_n: int = 3
    variant: str = "sweep_choch"    # "choch" | "sweep_choch"
    htf: str = "15m"
    htf_swing_n: int = 3
    lookback: int = 30              # setup candles for the sweep and the stop
    rr: float = 1.5
    buffer: float = 0.20            # price units beyond the swing for the stop
    min_risk_spreads: float = 2.0
    max_risk: float = 15.0
    max_hold_min: int = 120
    session: tuple[int, int] = (7, 20)
    spread: float = 0.54
    slippage: float = 0.05
    risk_usd: float = 100.0
    equity: float = 10_000.0


def resample(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    if tf == "1m":
        return m1.reset_index(drop=True)
    r = m1.set_index("timestamp").resample(f"{TF_MIN[tf]}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["open"])
    return r.reset_index()


def htf_bias(setup: pd.DataFrame, htf: pd.DataFrame, setup_tf: str, htf_tf: str, n: int) -> np.ndarray:
    """Trend of the last higher-timeframe candle CLOSED by the time each setup candle closes."""
    st = structure(htf, n)
    h = pd.DataFrame({"close_time": pd.to_datetime(htf["timestamp"], utc=True) + pd.Timedelta(minutes=TF_MIN[htf_tf]),
                      "trend": st["trend"].to_numpy()})
    s = pd.DataFrame({"close_time": pd.to_datetime(setup["timestamp"], utc=True)
                      + pd.Timedelta(minutes=TF_MIN[setup_tf])})
    m = pd.merge_asof(s, h, on="close_time", direction="backward")
    return m["trend"].fillna(0).to_numpy(int)


def signals(m1: pd.DataFrame, p: ScalpParams, htf: pd.DataFrame | None = None) -> pd.DataFrame:
    """Signals at setup-candle close: columns time (close time), dir (+1/-1), stop."""
    setup = resample(m1, p.tf)
    htf = resample(m1, p.htf) if htf is None else htf
    st = structure(setup, p.swing_n)
    bias = htf_bias(setup, htf, p.tf, p.htf, p.htf_swing_n)
    close_time = pd.to_datetime(setup["timestamp"], utc=True) + pd.Timedelta(minutes=TF_MIN[p.tf])
    hour = (close_time - pd.Timedelta(seconds=1)).dt.hour.to_numpy()
    in_session = (hour >= p.session[0]) & (hour < p.session[1])
    ev = st["event"].to_numpy()
    low = setup["low"].rolling(p.lookback, min_periods=1).min().to_numpy()
    high = setup["high"].rolling(p.lookback, min_periods=1).max().to_numpy()
    long_ = (ev == "CHOCH_UP") & (bias == 1) & in_session
    short = (ev == "CHOCH_DOWN") & (bias == -1) & in_session
    if p.variant == "sweep_choch":
        sw = sweeps(setup, st, asia_range(setup))
        recent = lambda a: pd.Series(a).rolling(p.lookback, min_periods=1).max().to_numpy().astype(bool)
        long_ &= recent(sw["sweep_low"].to_numpy())
        short &= recent(sw["sweep_high"].to_numpy())
    elif p.variant != "choch":
        raise ValueError(f"unknown variant {p.variant!r}")
    idx = np.flatnonzero(long_ | short)
    d = np.where(long_[idx], 1, -1)
    stop = np.where(d == 1, low[idx] - p.buffer, high[idx] + p.buffer)
    return pd.DataFrame({"time": close_time.to_numpy()[idx], "dir": d, "stop": stop})


def simulate(m1: pd.DataFrame, sig: pd.DataFrame, p: ScalpParams, one_at_a_time: bool = True) -> pd.DataFrame:
    """Trade the signals on 1m candles (mid prices; bid = mid - spread/2, ask = mid + spread/2).
    one_at_a_time=False simulates every signal on its own (overlapping trades), e.g. to label a dataset.
    Output rows keep the signal's row label in column `signal`."""
    ts = pd.to_datetime(m1["timestamp"], utc=True).to_numpy()
    o, h, l, c = (m1[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    half, slip = p.spread / 2, p.slippage
    hold = np.timedelta64(p.max_hold_min, "m")
    rows, busy_until = [], -1
    entry_idx = np.searchsorted(ts, pd.to_datetime(sig["time"], utc=True).to_numpy(), side="left")
    for (label, s), k0 in zip(sig.iterrows(), entry_idx):
        if k0 >= len(ts) or (one_at_a_time and k0 <= busy_until):
            continue
        d = int(s["dir"])
        entry = o[k0] + d * (half + slip)
        risk = (entry - s["stop"]) * d
        if not (p.min_risk_spreads * p.spread <= risk <= p.max_risk):
            continue
        stop, target = s["stop"], entry + d * p.rr * risk
        exit_px, reason, ambiguous, k = None, "", False, k0
        for k in range(k0, len(ts)):
            # exit side: bid for a long, ask for a short
            lo_x, hi_x, op_x = l[k] - d * half, h[k] - d * half, o[k] - d * half
            if k > k0 and ((d == 1 and op_x <= stop) or (d == -1 and op_x >= stop)):
                exit_px, reason = op_x - d * slip, "STOP_GAP"
                break
            if k > k0 and ((d == 1 and op_x >= target) or (d == -1 and op_x <= target)):
                exit_px, reason = op_x, "TARGET_GAP"
                break
            stop_hit = lo_x <= stop if d == 1 else hi_x >= stop
            tgt_hit = hi_x >= target if d == 1 else lo_x <= target
            if stop_hit:
                exit_px, reason, ambiguous = stop - d * slip, "STOP", bool(tgt_hit)
                break
            if tgt_hit:
                exit_px, reason = target, "TARGET"
                break
            if ts[k] + np.timedelta64(1, "m") - ts[k0] >= hold:
                exit_px, reason = c[k] - d * half - d * slip, "TIME"
                break
        if exit_px is None:
            exit_px, reason = c[k] - d * half - d * slip, "END_OF_DATA"
        r = (exit_px - entry) * d / risk
        cost = p.spread + slip * (1 + (reason not in ("TARGET", "TARGET_GAP")))
        rows.append({"signal": label, "entry_time": pd.Timestamp(ts[k0]), "exit_time": pd.Timestamp(ts[k]), "dir": d,
                     "entry": entry, "stop": stop, "target": target, "exit": exit_px, "risk": risk,
                     "r": r, "gross_r": r + cost / risk, "pnl_usd": r * p.risk_usd, "reason": reason,
                     "ambiguous": ambiguous})
        busy_until = k
    return pd.DataFrame(rows)


def summarize(tr: pd.DataFrame, p: ScalpParams) -> dict:
    if not len(tr):
        return {"trades": 0}
    eq = p.equity + tr["pnl_usd"].cumsum()
    peak = np.maximum.accumulate(np.r_[p.equity, eq.to_numpy()])[1:]
    losses = (tr["r"] <= 0).astype(int)
    streak = losses.groupby((losses != losses.shift()).cumsum()).cumsum().max()
    wins, loss = tr.loc[tr["r"] > 0, "pnl_usd"].sum(), -tr.loc[tr["r"] <= 0, "pnl_usd"].sum()
    hour = pd.to_datetime(tr["entry_time"], utc=True).dt.hour
    by = lambda g: {str(k): {"trades": int(len(v)), "expectancy_r": round(float(v["r"].mean()), 3),
                             "net_usd": round(float(v["pnl_usd"].sum()), 0)} for k, v in tr.groupby(g)}
    return {"trades": int(len(tr)), "win_rate_pct": round(100 * float((tr["r"] > 0).mean()), 1),
            "expectancy_r": round(float(tr["r"].mean()), 3),
            "gross_expectancy_r": round(float(tr["gross_r"].mean()), 3),
            "avg_cost_r": round(float((tr["gross_r"] - tr["r"]).mean()), 3),
            "avg_risk_price": round(float(tr["risk"].mean()), 2),
            "net_usd": round(float(tr["pnl_usd"].sum()), 0),
            "profit_factor": round(float(wins / loss), 2) if loss > 0 else None,
            "max_drawdown_pct": round(float(((eq - peak) / peak).min() * 100), 1),
            "max_consecutive_losses": int(streak),
            "ambiguous_stop_and_target_bars": int(tr["ambiguous"].sum()),
            "exit_reasons": {str(k): int(v) for k, v in tr["reason"].value_counts().items()},
            "by_direction": by(np.where(tr["dir"] == 1, "LONG", "SHORT")),
            "by_session": by(np.where(hour < 12, "LONDON", "NEW_YORK")),
            "by_year": by(pd.to_datetime(tr["entry_time"], utc=True).dt.year)}


def params_dict(p: ScalpParams) -> dict:
    return asdict(p)
