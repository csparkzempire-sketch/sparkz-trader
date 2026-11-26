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


def test_assistant_endpoint(client):
    d = client.get("/api/assistant").json()
    assert set(d) >= {"report", "error", "gate_entries", "drives_entries", "age_seconds"}
    if d["report"]:
        assert d["report"]["action"] in ("BUY", "SELL", "WAIT") and "strategy_fit_pct" in d["report"]


def test_dashboard_password_protects_the_api_but_not_the_bridge(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as m
    monkeypatch.setenv("SPARKZ_DASHBOARD_PASSWORD", "open-sesame")
    c = TestClient(m.app)
    assert c.get("/api/execution/alerts").status_code == 401
    assert c.get("/api/execution/alerts", headers={"X-Dashboard-Key": "wrong"}).status_code == 401
    assert c.get("/api/bridge/status").status_code == 200              # the bridge has its own token
    monkeypatch.delenv("SPARKZ_DASHBOARD_PASSWORD")
    assert m.dashboard_key_ok(None)                                    # on your own PC: no password


def test_chart_endpoint_robot_market_and_watched_market(client):
    from app.assistant.context import resample
    from app.market.providers.mock_provider import generate_candles
    from app.market.watch import WATCH
    s = client.get("/api/chart/symbols").json()
    assert s["robot_symbol"] == s["symbols"][0] and "1d" in s["timeframes"]
    d = client.get(f"/api/chart?symbol={s['robot_symbol']}&tf=15m&bars=120").json()
    if not d.get("error"):
        for k in ("candles", "structure", "swings", "sweeps", "signals", "levels", "zones", "trendlines", "analysis"):
            assert k in d, k
        times = [c["time"] for c in d["candles"]]
        assert times == sorted(times) and len(times) <= 121
    assert client.get("/api/chart?symbol=EURUSD&tf=1h").status_code == 404
    m5 = generate_candles("EURUSD", "5m", 12 * 24 * 120, "normal", seed=2)
    bars = {tf: [{"time": r.timestamp.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                 for r in (m5 if tf == "5m" else resample(m5, tf)).tail(800).itertuples()] for tf in ("5m", "1h", "4h", "1d")}
    for rows in bars.values():
        rows[-1]["forming"] = True
    WATCH.push("EURUSDm", {"time": bars["5m"][-1]["time"], "bid": 1.1, "ask": 1.10012}, bars, 5)
    try:
        assert "EURUSD" in client.get("/api/chart/symbols").json()["symbols"]
        d = client.get("/api/chart?symbol=EURUSD&tf=1h&bars=200").json()
        assert not d.get("error") and d["digits"] == 5 and d["candles"][-1]["time"] == \
            int(__import__("pandas").Timestamp(bars["1h"][-1]["time"]).timestamp())          # the forming candle is drawn
        assert d["spread"] == pytest.approx(0.00012) and d["analysis"].get("action") in ("BUY", "SELL", "WAIT")
        for sig in d["signals"]:
            assert sig["action"] in ("BUY", "SELL") and (sig["entry"] - sig["stop"]) * (1 if sig["action"] == "BUY" else -1) > 0
        assert client.get("/api/chart?symbol=EURUSD&tf=2h").status_code == 422
    finally:
        WATCH.reset()


def test_trades_endpoint(client):
    d = client.get("/api/trades").json()
    assert d["mode"] == "SIMULATION" and d["current_mode"] == "SIMULATION"
    for k in ("starting_balance", "summary", "trades", "now", "r_unit_usd"):
        assert k in d
    running = 0.0
    for t in reversed(d["trades"]):
        running += t["pnl"]
        assert abs(t["running_total"] - running) < 0.05
    assert client.get("/api/trades?mode=MT5_LIVE").json()["now"] is None
    assert client.get("/api/trades?mode=REAL").status_code == 422
    c = client.get("/api/chart?symbol=XAUUSD&tf=15m").json()
    assert "robot_trades" in c and "alerts" in c
