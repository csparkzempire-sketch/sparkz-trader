"""Phase 10: the API over the research engine."""

from __future__ import annotations

import os
import subprocess
import sys

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.data.repository import save_candles
from tests.conftest import make_candles


@pytest.fixture
def client():
    from app.main import app

    save_candles(make_candles(1500, drift=0.02, vol=1.5, seed=41), "XAUUSD", "15m", "test")
    return TestClient(app)


def test_health_and_config(client):
    assert client.get("/health").json()["live_trading_enabled"] is False
    cfg = client.get("/strategy/config").json()
    assert cfg["sizing"]["mode"] == "FIXED" and cfg["sizing"]["allow_martingale"] is False
    keys = [p["key"] for p in client.get("/strategy/presets").json()]
    assert "E_martingale" in keys and "0_control_single_position" in keys


def test_validation_refuses_unsafe_settings(client):
    r = client.post("/strategy/validate", json={"overrides": {"sizing": {"mode": "MARTINGALE"}}})
    assert r.status_code == 422 and "HIGH RISK" in str(r.json())
    r = client.post("/strategy/validate", json={"overrides": {"live_trading_enabled": True}})
    assert r.status_code == 422
    r = client.post("/strategy/validate", json={"overrides": {"risk": {"max_positions": 50}}})
    assert r.status_code == 422


def test_market_endpoints(client):
    inv = client.get("/market/inventory").json()
    assert inv[0]["symbol"] == "XAUUSD" and inv[0]["bars"] == 1500
    c = client.get("/market/candles", params={"limit": 100}).json()
    assert len(c) == 100 and "regime" in c[-1]
    a = client.get("/market/analysis").json()
    assert a["signal"] in ("BUY", "SELL", "NONE") and a["regime"]
    assert client.get("/market/candles", params={"symbol": "EURUSD"}).status_code == 404


def test_backtest_run_list_get_and_baskets(client):
    r = client.post("/backtest/run", json={"preset": "B_atr_grid", "name": "api test"}).json()
    assert r["id"] and r["metrics"]["baskets"] >= 1 and r["curve"]
    listed = client.get("/backtest/list").json()
    assert listed[0]["id"] == r["id"]
    assert client.get(f"/backtest/{r['id']}").json()["metrics"]["net_pnl"] == r["metrics"]["net_pnl"]
    bs = client.get(f"/baskets/backtest/{r['id']}").json()
    assert len(bs) == r["metrics"]["baskets"] and bs[0]["entries"]
    assert client.get("/backtest/999999").status_code == 404


def test_compare_endpoint(client):
    out = client.post("/backtest/compare", json={"presets": ["0_control_single_position", "B_atr_grid"]}).json()
    assert [r["key"] for r in out["rows"]] == ["0_control_single_position", "B_atr_grid"]


def test_paper_endpoints(client, monkeypatch):
    import app.paper.simulator as sim

    data = make_candles(2600, seed=42)
    monkeypatch.setattr(sim, "download", lambda sym, tf: (data, {"source": "test"}))
    assert client.post("/paper/accounts", json={"name": "demo", "preset": "B_atr_grid"}).status_code == 200
    assert client.post("/paper/accounts", json={"name": "demo"}).status_code == 409
    step = client.post("/paper/demo/step").json()
    assert step["bars_processed"] >= 1
    st = client.get("/paper/demo").json()
    assert st["balance"] == 10_000 and st["strategy"] == "B: ATR grid"
    assert client.get("/paper/nope").status_code == 404
    assert client.get("/risk/status").json()[0]["emergency"] == "OK"
    assert client.get("/risk/limits").json()["hard_max_positions"] == 20


def test_api_refuses_to_start_with_live_trading():
    env = {**os.environ, "LIVE_TRADING": "true"}
    r = subprocess.run([sys.executable, "-c", "import app.main"], env=env, capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(__file__)))
    assert r.returncode != 0 and "Live trading is not implemented" in r.stderr
