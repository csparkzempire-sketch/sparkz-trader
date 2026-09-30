"""Tests for the research studies module and GET /research."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.research.studies as studies
from app.main import app


@pytest.fixture
def cached(monkeypatch, synthetic_ohlcv):
    monkeypatch.setattr(studies, "load_processed", lambda symbol, timeframe: synthetic_ohlcv.copy())


def test_market_study_reports_every_side_with_periods(cached):
    rows = studies.market_study("EURUSD=X", "EUR/USD", "2023-01-01")
    hourly = [r for r in rows if r["timeframe"] == "1h"]
    assert {r["side"] for r in hourly} == {"both", "buy", "sell"}
    for r in hourly:
        assert r["full"]["trades"] >= 0 and r["periods"]["count"] == len(r["periods"]["rows"])
        assert 0 <= r["periods"]["profitable"] <= r["periods"]["count"]


def test_research_endpoint_serves_saved_results(tmp_path, monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(studies, "RESULTS_PATH", tmp_path / "results.json")
    monkeypatch.setattr("app.api.routes_research.load_results", lambda: studies.load_results(tmp_path / "results.json"))
    assert client.get("/research").status_code == 404

    studies.save({"generated_at": "x", "markets": [], "sensitivity": []}, tmp_path / "results.json")
    assert client.get("/research").json()["generated_at"] == "x"


def test_committed_results_cover_every_paper_account():
    results = studies.load_results()
    assert results is not None, "app/research/results.json should be committed"
    assert {s["account"] for s in results["sensitivity"]} == {a[0] for a in studies.ACCOUNT_SETUPS}
