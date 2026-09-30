"""
Optional ML research (phase 12): can the market conditions at the moment a
basket starts tell us P(favourable basket outcome)?

Dataset: one row per basket from a backtest of the rule-based strategy.
  features  the analysis of the SIGNAL bar (the last closed bar before the
            basket opened), made direction-relative where it matters, so
            "price above EMA20" means "in favour of the basket" for BUY and SELL alike
  label     1 if the basket closed with a profit

Honesty rules:
- splits are chronological (60% train / 20% validation / 20% test), never shuffled;
- purge: a training basket that was still open when validation began is dropped
  (its outcome wasn't known yet), and the same between validation and test;
- the filter threshold is chosen on validation, then applied once to test;
- results are compared with the base rate and with "keep every basket".
A model that can't beat those on the test window is reported as such.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.backtest.runner import prepare_features, run_backtest
from app.config import Settings
from app.data.repository import load_candles

FEATURES = ["rsi_dir", "adx", "di_spread_dir", "atr_pct", "vol_percentile", "bb_width", "bb_pctb_dir", "macd_hist_atr_dir",
            "ret_recent_dir", "dist_fast_dir", "dist_slow_dir", "dist_trend_dir", "body_share", "realized_vol", "hour"]


def basket_dataset(baskets, features: pd.DataFrame) -> pd.DataFrame:
    ts = features["timestamp"]
    rows = []
    for b in baskets:
        k = int(ts.searchsorted(pd.Timestamp(b.opened_at))) - 1      # last bar closed before the open
        if k < 0:
            continue
        r = features.iloc[k]
        s = 1 if b.direction == "BUY" else -1
        rows.append({
            "opened_at": b.opened_at, "closed_at": b.closed_at, "pnl": b.pnl, "label": int(b.pnl > 0),
            "rsi_dir": s * (r["rsi"] - 50), "adx": r["adx"], "di_spread_dir": s * (r["plus_di"] - r["minus_di"]),
            "atr_pct": r["atr_pct"], "vol_percentile": r["vol_percentile"], "bb_width": r["bb_width"],
            "bb_pctb_dir": s * (r["bb_pctb"] - 0.5), "macd_hist_atr_dir": s * r["macd_hist"] / r["atr"],
            "ret_recent_dir": s * r["ret_recent"], "dist_fast_dir": s * r["dist_ema_fast_atr"],
            "dist_slow_dir": s * r["dist_ema_slow_atr"], "dist_trend_dir": s * r["dist_ema_trend_atr"],
            "body_share": r["body_share"], "realized_vol": r["realized_vol"], "hour": pd.Timestamp(r["timestamp"]).hour,
        })
    return pd.DataFrame(rows).dropna().reset_index(drop=True)


def chrono_split(df: pd.DataFrame, fracs=(0.6, 0.2)) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    n = len(df)
    a, b = int(n * fracs[0]), int(n * (fracs[0] + fracs[1]))
    train, val, test = df.iloc[:a], df.iloc[a:b], df.iloc[b:]
    train = train[train["closed_at"] < val["opened_at"].iloc[0]] if len(val) else train
    val = val[val["closed_at"] < test["opened_at"].iloc[0]] if len(test) else val
    return train, val, test


def _eval(model, train, val, test) -> dict:
    X, y = train[FEATURES], train["label"]
    model.fit(X, y)
    pv, pt = model.predict_proba(val[FEATURES])[:, 1], model.predict_proba(test[FEATURES])[:, 1]
    # threshold chosen on validation: the one that maximises validation P&L of kept baskets
    cands = np.unique(np.quantile(pv, np.linspace(0, 0.8, 9)))
    best = max(cands, key=lambda t: val["pnl"][pv >= t].sum())
    kept = test[pt >= best]
    auc = roc_auc_score(test["label"], pt) if test["label"].nunique() == 2 else None
    return {
        "test_auc": auc, "test_brier": brier_score_loss(test["label"], pt),
        "base_rate_brier": brier_score_loss(test["label"], np.full(len(test), train["label"].mean())),
        "threshold_from_validation": float(best),
        "test_kept_baskets": int(len(kept)), "test_kept_pnl": float(kept["pnl"].sum()),
        "test_kept_win_rate_pct": float(kept["label"].mean() * 100) if len(kept) else None,
    }


def ml_study(settings: Settings, candles: pd.DataFrame | None = None) -> dict:
    if candles is None:
        candles = load_candles(settings.market.symbol, settings.market.timeframe)
    f = prepare_features(candles, settings)
    res = run_backtest(settings, features=f)
    df = basket_dataset(res.baskets, f)
    if len(df) < 60:
        return {"baskets": len(df), "note": "fewer than 60 baskets: too few to train and test honestly"}
    train, val, test = chrono_split(df)
    out = {
        "baskets": len(df), "train": len(train), "validation": len(val), "test": len(test),
        "test_window": [str(test["opened_at"].iloc[0]), str(test["opened_at"].iloc[-1])],
        "train_win_rate_pct": float(train["label"].mean() * 100), "test_win_rate_pct": float(test["label"].mean() * 100),
        "test_all_baskets_pnl": float(test["pnl"].sum()),
        "models": {
            "logistic_regression": _eval(make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, C=0.5)), train, val, test),
            "random_forest": _eval(RandomForestClassifier(300, max_depth=4, min_samples_leaf=10, random_state=0), train, val, test),
        },
    }
    try:
        from xgboost import XGBClassifier   # optional dependency
        out["models"]["xgboost"] = _eval(XGBClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8,
                                                       eval_metric="logloss"), train, val, test)
    except ImportError:
        out["models_note"] = "xgboost not installed; logistic regression and random forest only"
    out["reading"] = ("Useful only if the model beats the base-rate Brier score AND the filtered test P&L beats "
                      "keeping every basket. With a few hundred baskets, differences are mostly noise.")
    return out
