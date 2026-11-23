"""
Is there a gold strategy that makes money AFTER the spread? A pre-registered search on 1h XAUUSD history.

  python -m app.research.edge_search [--spread 0.54] [--slippage 0.10]

Protocol (fixed before any result was seen):
- data: Dukascopy XAUUSD 1h mid candles (download_1h), 2008 onwards;
- three periods: DEVELOPMENT 2008-2018 (every variant is run), VALIDATION 2019-2022 (only variants that passed
  development), HOLDOUT 2023-today (only variants that passed validation; looked at once);
- pass rule, development and validation alike: net Sharpe >= 0.5, alpha t-stat >= 2.0 (alpha = return not
  explained by simply holding gold: daily strategy returns regressed on gold's daily returns), at least 30 trades;
- costs: every change of position pays half the spread plus slippage per unit traded, at the bid/ask
  (spread 0.54 price units by default, the measured Dukascopy median; --spread 1.0 for a stress test). A spread
  in price units is a larger fraction of the price in 2008 (gold ~900) than today: conservative;
- the position is decided on a closed candle and held from that candle's close (no look-ahead: signals only
  use data up to and including the decision candle); results in % of price (Sharpe) and in USD at 0.01 lot
  (1 oz), with the long and short legs reported apart;
- a fixed list of 18 variants from 6 families; nothing is added after seeing results. Reported: every
  variant, so the number of tries is visible (with 18 tries, one t-stat near 2 is expected by chance).

Writes reports/studies/edge_search.json. Research only: simulation.
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

from app.config import REPORTS_DIR
from app.market.history import load_history

PERIODS = {"development": ("2008-01-01", "2019-01-01"), "validation": ("2019-01-01", "2023-01-01"),
           "holdout": ("2023-01-01", "2100-01-01")}
PASS = {"sharpe": 0.5, "alpha_t": 2.0, "trades": 30}


# ---------------------------------------------------------------------------------------------- helpers
def daily(h1: pd.DataFrame) -> pd.DataFrame:
    """1h -> daily candles on the 22:00 UTC roll (the forex day), labelled by the day they close."""
    d = h1.set_index("timestamp").resample("24h", offset="22h", label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return d


def to_hourly(pos_daily: pd.Series, h1: pd.DataFrame) -> pd.Series:
    """A daily position (decided at the daily close) held over the following hours."""
    idx = pd.DatetimeIndex(h1["timestamp"]) + pd.Timedelta(hours=1)       # each hourly candle's close time
    p = pos_daily.reindex(pos_daily.index.union(idx)).ffill().reindex(idx).fillna(0.0)
    return pd.Series(p.to_numpy(), index=h1.index)


def atr(d: pd.DataFrame, n: int = 14) -> pd.Series:
    tr = pd.concat([d["high"] - d["low"], (d["high"] - d["close"].shift()).abs(),
                    (d["low"] - d["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def rsi(c: pd.Series, n: int) -> pd.Series:
    ch = c.diff()
    up, dn = ch.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-ch.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


# ---------------------------------------------------------------------------------------------- families
def donchian(d, n, long_only=False):
    """Close above the n-day high: long until a close below the n/2-day low (mirror for shorts)."""
    hi, lo = d["high"].rolling(n).max().shift(), d["low"].rolling(n).min().shift()
    xhi, xlo = d["high"].rolling(n // 2).max().shift(), d["low"].rolling(n // 2).min().shift()
    pos, out = 0.0, []
    for c, a, b, xa, xb in zip(d["close"], hi, lo, xhi, xlo):
        if pos <= 0 and c > a:
            pos = 1.0
        elif pos >= 0 and c < b and not long_only:
            pos = -1.0
        elif pos > 0 and c < xb:
            pos = 0.0
        elif pos < 0 and c > xa:
            pos = 0.0
        out.append(pos)
    return pd.Series(out, index=d.index)


def ema_cross(d, f, s):
    c = d["close"]
    return np.sign(c.ewm(span=f, adjust=False).mean() - c.ewm(span=s, adjust=False).mean())


def tsmom(d, lb):
    return np.sign(d["close"] / d["close"].shift(lb) - 1).fillna(0.0)


def rsi2(d):
    """Connors-style: buy a 2-day RSI dip below 10 above the 200-day average, out above the 5-day average;
    mirror for shorts below the 200-day average."""
    c, r = d["close"], rsi(d["close"], 2)
    sma200, sma5 = c.rolling(200).mean(), c.rolling(5).mean()
    pos, out = 0.0, []
    for ci, ri, a, b in zip(c, r, sma200, sma5):
        if pos == 0 and not np.isnan(a):
            if ci > a and ri < 10:
                pos = 1.0
            elif ci < a and ri > 90:
                pos = -1.0
        elif pos > 0 and ci > b:
            pos = 0.0
        elif pos < 0 and ci < b:
            pos = 0.0
        out.append(pos)
    return pd.Series(out, index=d.index)


def seasonal_hours(h1, min_t=2.0):
    """Hold, each UTC hour, the sign of that hour's average return in all EARLIER calendar years (from 2 years of
    history on), only for hours whose t-stat there is at least min_t. Walk-forward: year Y uses years < Y."""
    ts = pd.DatetimeIndex(h1["timestamp"])
    ret = h1["close"].pct_change().shift(-1)         # return of the NEXT hour, held from this close
    hour_next = (ts + pd.Timedelta(hours=1)).hour
    years = ts.year
    pos = pd.Series(0.0, index=h1.index)
    for y in sorted(set(years)):
        past = (years < y) & (years >= y - 10)
        if len(set(years[past])) < 2:
            continue
        g = pd.DataFrame({"h": hour_next[past], "r": ret[past]}).dropna().groupby("h")["r"]
        t = g.mean() / (g.std() / np.sqrt(g.count()))
        sig = np.sign(t).where(t.abs() >= min_t, 0.0)
        cur = years == y
        pos[cur] = pd.Series(hour_next[cur]).map(sig).fillna(0.0).to_numpy()
    return pos


def session_breakout(h1, start_h, end_h, exit_h):
    """Range of [start_h, end_h) UTC; after end_h, long on a close above it / short below; flat at exit_h."""
    ts = pd.DatetimeIndex(h1["timestamp"])
    hour = ts.hour
    day = ts.normalize()
    pos, out, rng = 0.0, [], {}
    for i, (d, h, hi, lo, c) in enumerate(zip(day, hour, h1["high"], h1["low"], h1["close"])):
        r = rng.setdefault(d, [np.inf, -np.inf, False])
        close_hour = h + 1                                # this candle closes at h+1
        if start_h <= h < end_h:
            r[0], r[1] = min(r[0], lo), max(r[1], hi)
        if close_hour >= exit_h or h < start_h:
            pos = 0.0
        elif h >= end_h and pos == 0 and not r[2] and np.isfinite(r[0]):
            if c > r[1]:
                pos, r[2] = 1.0, True
            elif c < r[0]:
                pos, r[2] = -1.0, True
        out.append(pos)
    return pd.Series(out, index=h1.index)


def variants(h1: pd.DataFrame, d: pd.DataFrame) -> dict:
    v = {}
    for n in (20, 55, 100):
        v[f"donchian_{n}"] = ("trend", to_hourly(donchian(d, n), h1))
    v["donchian_55_long_only"] = ("trend", to_hourly(donchian(d, 55, long_only=True), h1))
    for f, s in ((10, 50), (20, 100), (50, 200)):
        v[f"ema_{f}_{s}"] = ("trend", to_hourly(ema_cross(d, f, s), h1))
    for lb in (21, 63, 126, 252):
        v[f"tsmom_{lb}d"] = ("momentum", to_hourly(tsmom(d, lb), h1))
    v["rsi2_mean_reversion"] = ("mean_reversion", to_hourly(rsi2(d), h1))
    v["seasonal_hours_t2"] = ("seasonality", seasonal_hours(h1, 2.0))
    v["seasonal_hours_t3"] = ("seasonality", seasonal_hours(h1, 3.0))
    v["london_breakout"] = ("session", session_breakout(h1, 7, 8, 16))
    v["new_york_breakout"] = ("session", session_breakout(h1, 13, 14, 20))
    v["asia_range_london_breakout"] = ("session", session_breakout(h1, 0, 7, 16))
    v["buy_and_hold"] = ("benchmark", pd.Series(1.0, index=h1.index))
    return v


# ---------------------------------------------------------------------------------------------- evaluation
def evaluate(h1: pd.DataFrame, pos: pd.Series, spread: float, slip: float, start, end) -> dict:
    ts = pd.DatetimeIndex(h1["timestamp"])
    m = (ts >= pd.Timestamp(start, tz="UTC")) & (ts < pd.Timestamp(end, tz="UTC"))
    c = h1["close"].to_numpy()
    p = pos.to_numpy()
    nxt = np.append(c[1:], c[-1])
    move = nxt - c                                           # price change while the position is held
    prev = np.append(0.0, p[:-1])
    turn = np.abs(p - prev)
    usd = p * move - turn * (spread / 2 + slip)              # per 1 oz (0.01 lot)
    gross_usd = p * move
    pct = usd / c
    df = pd.DataFrame({"t": ts, "usd": usd, "gross": gross_usd, "pct": pct, "p": p, "turn": turn,
                       "bh": move / c})[m]
    if not len(df):
        return {"hours": 0}
    day = df.set_index("t").resample("1D")[["pct", "bh", "usd"]].sum()
    day = day[(day != 0).any(axis=1)]
    sr = day["pct"].mean() / day["pct"].std() * np.sqrt(252) if day["pct"].std() > 0 else 0.0
    x, y = day["bh"].to_numpy(), day["pct"].to_numpy()
    beta = float(np.cov(x, y)[0, 1] / np.var(x, ddof=1)) if np.var(x) > 0 else 0.0
    resid = y - beta * x
    alpha = resid.mean()
    alpha_t = alpha / (resid.std(ddof=1) / np.sqrt(len(resid))) if resid.std() > 0 else 0.0
    eq = df["usd"].cumsum()
    entries = int(((df["p"] != 0) & (df["p"].shift(fill_value=0) != df["p"])).sum())
    yrs = df.groupby(df["t"].dt.year)["usd"].sum()
    return {"hours": int(len(df)), "trades": entries, "time_in_market_pct": round(100 * float((df["p"] != 0).mean()), 1),
            "net_usd_0.01lot": round(float(df["usd"].sum()), 0), "gross_usd_0.01lot": round(float(df["gross"].sum()), 0),
            "cost_usd_0.01lot": round(float((df["gross"] - df["usd"]).sum()), 0),
            "long_usd": round(float(df.loc[df["p"] > 0, "usd"].sum()), 0),
            "short_usd": round(float(df.loc[df["p"] < 0, "usd"].sum()), 0),
            "sharpe": round(float(sr), 2), "annual_return_pct": round(float(day["pct"].mean() * 252 * 100), 2),
            "beta_to_gold": round(beta, 2), "alpha_annual_pct": round(float(alpha * 252 * 100), 2),
            "alpha_t": round(float(alpha_t), 2), "max_drawdown_usd": round(float((eq - eq.cummax()).min()), 0),
            "positive_years": f"{int((yrs > 0).sum())}/{len(yrs)}",
            "by_year_usd": {int(k): round(float(v), 0) for k, v in yrs.items()}}


def passes(r: dict) -> bool:
    return (r.get("sharpe", 0) >= PASS["sharpe"] and r.get("alpha_t", 0) >= PASS["alpha_t"]
            and r.get("trades", 0) >= PASS["trades"])


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spread", type=float, default=0.54)
    ap.add_argument("--slippage", type=float, default=0.10)
    ap.add_argument("--out", default="edge_search")
    a = ap.parse_args(argv)
    t0 = time.time()
    h1 = load_history("XAUUSD", "1h", "dukascopy").sort_values("timestamp").reset_index(drop=True)
    d = daily(h1)
    print(f"{len(h1)} hourly candles {h1['timestamp'].iloc[0]:%Y-%m-%d} .. {h1['timestamp'].iloc[-1]:%Y-%m-%d}, "
          f"{len(d)} days", flush=True)
    vs = variants(h1, d)
    out = {"meta": {"spread": a.spread, "slippage": a.slippage, "periods": PERIODS, "pass_rule": PASS,
                    "variants_tried": len(vs) - 1,
                    "data": f"{h1['timestamp'].iloc[0]} .. {h1['timestamp'].iloc[-1]}"}, "variants": {}}
    for name, (fam, pos) in vs.items():
        res = {"family": fam}
        dev = evaluate(h1, pos, a.spread, a.slippage, *PERIODS["development"])
        res["development"] = dev
        stage = "development"
        if passes(dev) or fam == "benchmark":
            res["validation"] = evaluate(h1, pos, a.spread, a.slippage, *PERIODS["validation"])
            stage = "validation"
            if passes(res["validation"]) or fam == "benchmark":
                res["holdout"] = evaluate(h1, pos, a.spread, a.slippage, *PERIODS["holdout"])
                stage = "holdout"
        res["reached"] = stage
        res["passed_all"] = fam != "benchmark" and stage == "holdout" and passes(res["holdout"])
        res["full_period_for_reference"] = evaluate(h1, pos, a.spread, a.slippage, "2008-01-01", "2100-01-01")
        out["variants"][name] = res
        dv = res["development"]
        print(f"{name:28s} dev: sharpe {dv.get('sharpe'):>5} alpha_t {dv.get('alpha_t'):>5} trades {dv.get('trades'):>5} "
              f"net {dv.get('net_usd_0.01lot'):>7} -> {stage}" + (" PASSED ALL" if res["passed_all"] else ""), flush=True)
    out["survivors"] = [k for k, v in out["variants"].items() if v["passed_all"]]
    (REPORTS_DIR / "studies").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "studies" / f"{a.out}.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"survivors: {out['survivors']} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
