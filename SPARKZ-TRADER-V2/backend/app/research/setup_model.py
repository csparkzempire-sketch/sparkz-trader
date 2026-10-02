"""
A model that filters the structure-scalping setups (scalp.py): it estimates each setup's result in R after
costs from what is known when the setup appears, and only setups it expects to be profitable are traded.

  dataset      every setup signal, simulated ON ITS OWN (overlapping trades allowed) for its label: the net R
               of the trade after spread and slippage. Features use only candles closed by the signal.
  walk-forward from --start, each month is predicted by a model trained on all EARLIER trades whose exit
               happened before that month began (a purge, so no label overlaps the test month).
  decision     trade a setup when the predicted R exceeds --threshold (default 0: expected to beat its
               costs). The chosen setups are then traded one at a time, exactly like the unfiltered setup,
               and both are reported side by side on the same months.

  python -m app.research.setup_model [--tf 1m --swing-n 3 --variant choch --rr 2.0] [--start 2025-01-01]

Writes reports/studies/setup_model_<source>_<tf>.json. Simulation only; nothing here can place an order.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace

import numpy as np
import pandas as pd

from app.config import REPORTS_DIR
from app.market.history import load_history
from app.research.scalp import TF_MIN, ScalpParams, params_dict, resample, signals, simulate, summarize
from app.research.structure import asia_range, structure, sweeps

FEATURES = ["dir", "hour_sin", "hour_cos", "weekday", "london", "risk_atr", "risk_spreads", "atr_ratio",
            "vol_60", "break_atr", "body_frac", "pullback_atr", "swept", "asia_dist_atr", "mom_15", "mom_60",
            "rsi", "htf_age", "htf_ema_slope", "htf_dist_atr", "day_range_atr"]


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=1).mean()


def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up, dn = d.clip(lower=0).rolling(n, min_periods=1).mean(), (-d.clip(upper=0)).rolling(n, min_periods=1).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def features(m1: pd.DataFrame, p: ScalpParams, sig: pd.DataFrame) -> pd.DataFrame:
    """One row per signal; every value uses only candles closed when the signal's setup candle closes."""
    setup = resample(m1, p.tf)
    st = structure(setup, p.swing_n)
    step = pd.Timedelta(minutes=TF_MIN[p.tf])
    label = pd.to_datetime(sig["time"], utc=True) - step
    i = np.searchsorted(pd.to_datetime(setup["timestamp"], utc=True).to_numpy(), label.to_numpy())
    d = sig["dir"].to_numpy(float)
    c, h, l, o = (setup[k].to_numpy(float) for k in ("close", "high", "low", "open"))
    atr = _atr(setup).to_numpy()
    entry_ref = c[i] + d * p.spread / 2
    risk = (entry_ref - sig["stop"].to_numpy()) * d
    swing = np.where(d == 1, np.r_[np.nan, st["sh"].to_numpy()[:-1]][i], np.r_[np.nan, st["sl"].to_numpy()[:-1]][i])
    sw = sweeps(setup, st, asia_range(setup))
    recent = lambda a: pd.Series(a).rolling(p.lookback, min_periods=1).max().to_numpy()[i]
    asia = asia_range(setup)
    asia_lvl = np.where(d == 1, asia["asia_high"].to_numpy()[i], asia["asia_low"].to_numpy()[i])
    ret = pd.Series(np.log(c))
    per_hour = 60 // TF_MIN[p.tf]
    vol60 = ret.diff().rolling(max(per_hour, 2), min_periods=2).std().to_numpy()[i]
    hour = (label + step - pd.Timedelta(seconds=1)).dt.hour.to_numpy()

    htf = resample(m1, p.htf)
    hst = structure(htf, p.htf_swing_n)
    hatr = _atr(htf)
    flip = (hst["trend"] != hst["trend"].shift()).cumsum()
    age = hst.groupby(flip).cumcount()
    ema = htf["close"].ewm(span=20, adjust=False).mean()
    hf = pd.DataFrame({"close_time": pd.to_datetime(htf["timestamp"], utc=True) + pd.Timedelta(minutes=TF_MIN[p.htf]),
                       "htf_age": age.to_numpy(), "htf_ema_slope": ((ema - ema.shift(4)) / hatr).to_numpy(),
                       "hatr": hatr.to_numpy(), "hsh": hst["sh"].to_numpy(), "hsl": hst["sl"].to_numpy()})
    hm = pd.merge_asof(pd.DataFrame({"close_time": label + step}).reset_index(drop=True), hf, on="close_time",
                       direction="backward")
    day = pd.to_datetime(setup["timestamp"], utc=True).dt.normalize()
    day_hi = setup["high"].groupby(day).cummax().to_numpy()[i]
    day_lo = setup["low"].groupby(day).cummin().to_numpy()[i]
    hatr_v = hm["hatr"].to_numpy()
    htf_level = np.where(d == 1, hm["hsh"].to_numpy(), hm["hsl"].to_numpy())
    back = lambda k: c[np.maximum(i - k, 0)]
    f = pd.DataFrame({
        "dir": d,
        "hour_sin": np.sin(2 * np.pi * hour / 24), "hour_cos": np.cos(2 * np.pi * hour / 24),
        "weekday": (label + step).dt.weekday.to_numpy(), "london": (hour < 12).astype(float),
        "risk_atr": risk / atr[i], "risk_spreads": risk / p.spread, "atr_ratio": atr[i] / hatr_v,
        "vol_60": vol60, "break_atr": d * (c[i] - swing) / atr[i],
        "body_frac": d * (c[i] - o[i]) / np.maximum(h[i] - l[i], 1e-9),
        "pullback_atr": (pd.Series(h).rolling(p.lookback, min_periods=1).max().to_numpy()[i]
                         - pd.Series(l).rolling(p.lookback, min_periods=1).min().to_numpy()[i]) / atr[i],
        "swept": np.where(d == 1, recent(sw["sweep_low"].to_numpy()), recent(sw["sweep_high"].to_numpy())),
        "asia_dist_atr": d * (asia_lvl - c[i]) / hatr_v,
        "mom_15": d * (c[i] - back(max(15 // TF_MIN[p.tf], 1))) / hatr_v,
        "mom_60": d * (c[i] - back(per_hour)) / hatr_v,
        "rsi": np.where(d == 1, 1, -1) * (_rsi(setup["close"]).to_numpy()[i] - 50),
        "htf_age": hm["htf_age"].to_numpy(float), "htf_ema_slope": d * hm["htf_ema_slope"].to_numpy(),
        "htf_dist_atr": d * (htf_level - c[i]) / hatr_v,
        "day_range_atr": (day_hi - day_lo) / hatr_v,
    }, index=sig.index)
    return f[FEATURES]


def dataset(m1: pd.DataFrame, p: ScalpParams) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(signals, labelled rows): features + entry/exit time + net R for every signal that becomes a trade."""
    sig = signals(m1, p)
    tr = simulate(m1, sig, p, one_at_a_time=False)
    f = features(m1, p, sig)
    rows = f.loc[tr["signal"]].reset_index(drop=True)
    rows[["signal", "entry_time", "exit_time", "r"]] = tr[["signal", "entry_time", "exit_time", "r"]].to_numpy()
    rows["r"] = rows["r"].astype(float)
    return sig, rows


def make_model(seed: int = 0):
    from sklearn.ensemble import HistGradientBoostingRegressor

    # shallow and strongly regularised: the target is noisy and the edge, if any, is small
    return HistGradientBoostingRegressor(max_depth=3, learning_rate=0.03, max_iter=200, min_samples_leaf=200,
                                         l2_regularization=1.0, random_state=seed)


def walk_forward(rows: pd.DataFrame, start: str, min_train: int = 500, model_factory=make_model) -> pd.Series:
    """Out-of-sample predicted R for every row from `start`, retrained monthly on purged earlier rows."""
    et = pd.to_datetime(rows["entry_time"], utc=True)
    xt = pd.to_datetime(rows["exit_time"], utc=True)
    pred = pd.Series(np.nan, index=rows.index)
    months = pd.date_range(pd.Timestamp(start, tz="UTC"), et.max() + pd.offsets.MonthBegin(1), freq="MS")
    for m0, m1_ in zip(months[:-1], months[1:]):
        train = rows[xt < m0]                              # outcome known before the month begins
        test = (et >= m0) & (et < m1_)
        if len(train) < min_train or not test.any():
            continue
        mdl = model_factory()
        mdl.fit(train[FEATURES].to_numpy(float), train["r"].clip(-3, 5).to_numpy())
        pred[test] = mdl.predict(rows.loc[test, FEATURES].to_numpy(float))
    return pred


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="dukascopy", choices=["yahoo", "oanda", "mt5", "dukascopy"])
    ap.add_argument("--tf", default="1m")
    ap.add_argument("--swing-n", type=int, default=3)
    ap.add_argument("--variant", default="choch")
    ap.add_argument("--rr", type=float, default=2.0)
    ap.add_argument("--spread", type=float, default=0.54)
    ap.add_argument("--start", default="2025-01-01", help="first out-of-sample month")
    ap.add_argument("--threshold", type=float, default=0.0, help="trade when predicted net R exceeds this")
    a = ap.parse_args(argv)
    t0 = time.time()
    m1 = load_history("XAUUSD", "1m", a.source).reset_index(drop=True)
    p = replace(ScalpParams(spread=a.spread), tf=a.tf, swing_n=a.swing_n, variant=a.variant, rr=a.rr)
    sig, rows = dataset(m1, p)
    print(f"{len(sig)} signals, {len(rows)} labelled trades ({time.time() - t0:.0f}s)", flush=True)
    pred = walk_forward(rows, a.start)
    oos = pred.notna()
    start = pd.Timestamp(a.start, tz="UTC")
    sig_oos = sig[pd.to_datetime(sig["time"], utc=True) >= start]
    base = simulate(m1, sig_oos, p)
    out = {"meta": {"source": a.source, "params": params_dict(p), "oos_start": str(start),
                    "signals": len(sig), "labelled_trades": len(rows), "features": FEATURES,
                    "model": "HistGradientBoostingRegressor(max_depth=3, lr=0.03, 200 iters, min_samples_leaf=200)",
                    "target": "net R per trade (clipped to [-3, 5] for training)",
                    "walk_forward": "monthly retrain on trades whose exit precedes the month"},
           "unfiltered_oos": summarize(base, p), "filtered_oos": {}}
    r_oos, p_oos = rows.loc[oos, "r"], pred[oos]
    out["prediction_quality"] = {
        "oos_rows": int(oos.sum()),
        "spearman_pred_vs_r": round(float(p_oos.rank().corr(r_oos.rank())), 4),
        "mean_r_by_pred_quintile": [round(float(v), 3) for v in
                                    r_oos.groupby(pd.qcut(p_oos.rank(method="first"), 5, labels=False)).mean()]}
    for th in sorted({a.threshold, 0.0, 0.05, 0.1}):
        keep = rows.loc[oos & (pred > th), "signal"]
        tr = simulate(m1, sig.loc[sorted(set(keep))], p)
        out["filtered_oos"][str(th)] = summarize(tr, p)
        s = out["filtered_oos"][str(th)]
        print(f"threshold {th:+.2f}: {s.get('trades', 0)} trades, {s.get('expectancy_r', 0):+.3f}R, "
              f"net {s.get('net_usd', 0):+.0f} USD", flush=True)
    b = out["unfiltered_oos"]
    print(f"unfiltered: {b.get('trades', 0)} trades, {b.get('expectancy_r', 0):+.3f}R, net {b.get('net_usd', 0):+.0f} USD")
    print("prediction quality:", out["prediction_quality"])
    (REPORTS_DIR / "studies").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "studies" / f"setup_model_{a.source}_{a.tf}.json").write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
