"""Phase 12 (ML research) and the command line."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.backtest.runner import prepare_features, run_backtest
from app.cli import _overrides, main
from app.config import load_config
from app.research.ml import basket_dataset, chrono_split, ml_study
from tests.conftest import make_candles

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_chronological_split_never_shuffles_and_purges_overlap():
    rows = [{"opened_at": T0 + timedelta(hours=10 * i), "closed_at": T0 + timedelta(hours=10 * i + 15),
             "pnl": 1.0, "label": 1} for i in range(20)]
    tr, va, te = chrono_split(pd.DataFrame(rows))
    assert tr["opened_at"].max() < va["opened_at"].min() and va["opened_at"].max() < te["opened_at"].min()
    assert (tr["closed_at"] < va["opened_at"].iloc[0]).all()      # still-open train baskets were dropped
    assert (va["closed_at"] < te["opened_at"].iloc[0]).all()


def test_ml_features_come_from_the_bar_before_the_basket_opened():
    s = load_config(env={"GRID_MODE": "ATR"})
    f = prepare_features(make_candles(2000, drift=0.02, vol=1.5, seed=51), s)
    res = run_backtest(s, features=f)
    ds = basket_dataset(res.baskets, f)
    b = res.baskets[0]
    k = int(f["timestamp"].searchsorted(pd.Timestamp(b.opened_at))) - 1
    assert f["timestamp"].iloc[k] < pd.Timestamp(b.opened_at)
    sign = 1 if b.direction == "BUY" else -1
    assert ds["rsi_dir"].iloc[0] == pytest.approx(sign * (f["rsi"].iloc[k] - 50))


def test_ml_study_reports_against_baselines():
    s = load_config(env={"GRID_MODE": "ATR", "COOLDOWN_BARS_AFTER_LOSS": "0"})
    out = ml_study(s, candles=make_candles(9000, drift=0.0, vol=1.5, seed=52))
    if "note" in out:
        pytest.skip(out["note"])
    lr = out["models"]["logistic_regression"]
    assert {"test_auc", "test_brier", "base_rate_brier", "test_kept_pnl"} <= set(lr)
    assert out["train"] + out["validation"] + out["test"] <= out["baskets"]


def test_cli_overrides_and_backtest(capsys):
    ov = _overrides(["grid.atr_multiplier=0.8", "sizing.mode=LINEAR", "risk.max_positions=3"], "EURUSD", "1h")
    assert ov == {"grid": {"atr_multiplier": 0.8}, "sizing": {"mode": "LINEAR"}, "risk": {"max_positions": 3},
                  "market": {"symbol": "EURUSD", "timeframe": "1h"}}
    from app.data.repository import save_candles

    save_candles(make_candles(1200, seed=53), "XAUUSD", "15m", "test")
    main(["backtest", "--preset", "B_atr_grid", "--set", "risk.max_positions=3"])
    assert '"baskets"' in capsys.readouterr().out
