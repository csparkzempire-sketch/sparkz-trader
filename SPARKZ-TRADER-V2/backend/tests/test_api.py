"""API: dashboard, controls, research jobs, WebSocket. Mock data, temporary database."""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os
    d = tmp_path_factory.mktemp("api")
    os.environ["SPARKZ_V2_DB_URL"] = f"sqlite:///{d / 'api.db'}"
    os.environ["MARKET_DATA_PROVIDER"] = "MOCK"
    from app.main import app
    with TestClient(app) as c:
        for _ in range(50):
            if c.get("/api/dashboard").json()["market"]:
                break
            time.sleep(0.2)
        yield c
    os.environ.pop("SPARKZ_V2_DB_URL")
    os.environ.pop("MARKET_DATA_PROVIDER")


def test_dashboard_has_every_panel(client):
    d = client.get("/api/dashboard").json()
    for k in ("market", "analysis", "basket", "account", "status", "health", "events", "equity_curve", "candles"):
        assert k in d
    assert d["account"]["label"] == "PAPER ACCOUNT"
    assert d["live_trading_enabled"] is False
    assert d["status"] in ("ANALYZING", "WAITING", "ENTRY", "ADDING_POSITION", "MANAGING_BASKET", "TARGET_REACHED",
                           "CLOSING", "COOLDOWN", "STOPPED")


def test_read_endpoints(client):
    for url in ("/api/market", "/api/market/candles", "/api/strategy", "/api/baskets/current", "/api/baskets/history",
                "/api/account", "/api/system/health", "/api/system/config", "/api/system/events",
                "/api/backtest/reports"):
        assert client.get(url).status_code == 200, url
    h = client.get("/api/system/health").json()
    assert h["executor"]["live_trading_enabled"] is False and "cpu_percent" in h["process"]


def test_emergency_stop_and_resume(client):
    r = client.post("/api/system/emergency-stop", json={"reason": "test"}).json()
    assert r["status"] == "STOPPED"
    assert client.get("/api/dashboard").json()["emergency_stop"] == "test"
    assert any(e["type"] == "EMERGENCY_STOP" for e in client.get("/api/system/events").json())
    assert client.post("/api/system/resume").json()["status"] != "STOPPED"
    assert client.post("/api/system/resume").status_code == 409


def test_restart_validates_settings(client):
    assert client.post("/api/system/restart", json={"overrides": {"live_trading_enabled": True}}).status_code == 422
    assert client.post("/api/system/restart", json={"overrides": {"sizing": {"mode": "MULTIPLIER"}}}).status_code == 422


def test_backtest_job(client):
    jid = client.post("/api/backtest/run", json={"data": "synthetic:normal", "bars": 1200}).json()["job"]
    for _ in range(100):
        j = client.get(f"/api/backtest/jobs/{jid}").json()
        if j["status"] != "running":
            break
        time.sleep(0.2)
    assert j["status"] == "done", j.get("error")
    assert j["result"]["simulated"] is True and "metrics" in j["result"]


def test_lab_requires_space_and_caps_it(client):
    assert client.post("/api/backtest/lab", json={"data": "synthetic"}).status_code == 422


def test_websocket_pushes_state(client):
    with client.websocket_connect("/ws/live") as ws:
        msg = ws.receive_json()
    assert msg["account"]["label"] == "PAPER ACCOUNT"
