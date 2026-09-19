from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, synthetic_ohlcv, tmp_path):
    """
    TestClient wired to a temp SQLite DB and a temp models dir, with the
    network-dependent downloader replaced by synthetic data so tests never
    hit the internet.
    """
    import app.config as config_mod

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    config_mod.get_settings.cache_clear()

    import app.database.database as db_mod
    import importlib

    importlib.reload(db_mod)

    import app.ml.model_registry as registry
    monkeypatch.setattr(registry, "MODELS_DIR", tmp_path / "models")

    import app.backtest.report as report_mod
    monkeypatch.setattr(report_mod, "REPORTS_DIR", tmp_path / "reports")

    # Patch the downloader used by the routes to return our synthetic data,
    # regardless of requested symbol/timeframe, so no network call happens.
    import app.data.downloader as downloader_mod

    def _fake_download_ohlcv(symbol=None, timeframe=None, **kwargs):
        return synthetic_ohlcv.copy()

    monkeypatch.setattr(downloader_mod, "download_ohlcv", _fake_download_ohlcv)
    monkeypatch.setattr("app.api.routes_market.download_ohlcv", _fake_download_ohlcv)
    monkeypatch.setattr("app.api.routes_backtest.download_ohlcv", _fake_download_ohlcv)
    monkeypatch.setattr("app.api.routes_models.download_ohlcv", _fake_download_ohlcv)

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as c:
        yield c


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["live_trading_enabled"] is False


def test_market_data_endpoint(client):
    resp = client.get("/market/EURUSD=X?timeframe=1h&limit=50")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "EURUSD=X"
    assert body["count"] == 50
    assert len(body["candles"]) == 50


def test_latest_price_endpoint(client):
    resp = client.get("/market/EURUSD=X/latest?timeframe=1h")
    assert resp.status_code == 200
    body = resp.json()
    assert "close" in body


def test_backtest_endpoint_baseline(client):
    resp = client.post("/backtest", json={"symbol": "EURUSD=X", "timeframe": "1h", "strategy": "baseline"})
    assert resp.status_code == 200
    body = resp.json()
    assert "backtest_id" in body
    assert "total_return_pct" in body["metrics"]
    assert "buy_and_hold_return_pct" in body["baseline_comparison"]


def test_backtest_endpoint_rejects_unknown_model_strategy(client):
    resp = client.post("/backtest", json={"symbol": "EURUSD=X", "timeframe": "1h", "strategy": "nonexistent_model_id"})
    assert resp.status_code == 404


def test_train_and_predict_flow(client):
    train_resp = client.post(
        "/models/train",
        json={"symbol": "EURUSD=X", "timeframe": "1h", "model_type": "logistic_regression"},
    )
    assert train_resp.status_code == 200, train_resp.text
    body = train_resp.json()
    model_id = body["model_id"]
    assert "classification_metrics_test" in body

    predict_resp = client.post("/models/predict", json={"model_id": model_id, "symbol": "EURUSD=X", "timeframe": "1h"})
    assert predict_resp.status_code == 200, predict_resp.text
    pred_body = predict_resp.json()
    assert 0.0 <= pred_body["probability_up"] <= 1.0
    assert pred_body["signal"] in ("BUY", "SELL", "HOLD")

    list_resp = client.get("/models")
    assert list_resp.status_code == 200
    assert any(m["model_id"] == model_id for m in list_resp.json())


def test_predict_unknown_model_returns_404(client):
    resp = client.post("/models/predict", json={"model_id": "does_not_exist", "symbol": "EURUSD=X", "timeframe": "1h"})
    assert resp.status_code == 404


def test_paper_trading_flow(client):
    start_resp = client.post("/paper/start", json={"account_name": "test_acct", "starting_balance": 5000})
    assert start_resp.status_code == 200
    assert start_resp.json()["is_active"] is True

    account_resp = client.get("/paper/account?account_name=test_acct")
    assert account_resp.status_code == 200
    assert account_resp.json()["balance"] == 5000

    positions_resp = client.get("/paper/positions?account_name=test_acct")
    assert positions_resp.status_code == 200
    assert positions_resp.json() == []

    stop_resp = client.post("/paper/stop", params={"account_name": "test_acct"})
    assert stop_resp.status_code == 200
    assert stop_resp.json()["is_active"] is False


def test_paper_account_not_found_returns_404(client):
    resp = client.get("/paper/account?account_name=never_started")
    assert resp.status_code == 404


def test_paper_feed_start_status_stop(client):
    start_resp = client.post("/paper/start", json={"account_name": "feed_acct", "starting_balance": 10000})
    assert start_resp.status_code == 200

    feed_resp = client.post(
        "/paper/feed/start",
        json={
            "account_name": "feed_acct",
            "symbol": "EURUSD=X",
            "timeframe": "1h",
            "strategy": "baseline",
            "poll_interval_seconds": 3600,
        },
    )
    assert feed_resp.status_code == 200, feed_resp.text
    body = feed_resp.json()
    assert body["running"] is True
    assert body["symbol"] == "EURUSD=X"

    # Starting a second feed for the same account should be rejected.
    dup_resp = client.post(
        "/paper/feed/start",
        json={"account_name": "feed_acct", "symbol": "EURUSD=X", "timeframe": "1h", "poll_interval_seconds": 3600},
    )
    assert dup_resp.status_code == 409

    status_resp = client.get("/paper/feed/status?account_name=feed_acct")
    assert status_resp.status_code == 200

    stop_resp = client.post("/paper/feed/stop?account_name=feed_acct")
    assert stop_resp.status_code == 200
    assert stop_resp.json()["running"] is False


def test_paper_feed_status_404_when_never_started(client):
    resp = client.get("/paper/feed/status?account_name=nonexistent_feed")
    assert resp.status_code == 404
