"""Safety rails: no live trading, high-risk sizing gated, hard limits, emergency stop, account halt, stale data."""

import ast
from pathlib import Path

import pytest

from app.config import HARD_MAX_POSITIONS, Settings, load_settings
from app.execution.simulator import LiveExecutionAdapter
from app.market.instruments import get_instrument
from app.strategy.position_sizing import lots_for_entry
from tests.conftest import Driver

APP = Path(__file__).resolve().parents[1] / "app"


def test_live_trading_cannot_be_enabled():
    with pytest.raises(ValueError):
        Settings(live_trading_enabled=True)
    with pytest.raises(ValueError):
        load_settings(env={"LIVE_TRADING_ENABLED": "true"})
    assert load_settings(env={}).live_trading_enabled is False


def test_live_execution_adapter_refuses_to_exist():
    with pytest.raises(PermissionError):
        LiveExecutionAdapter()


def test_multiplier_sizing_disabled_by_default_and_flagged_high_risk():
    with pytest.raises(ValueError):
        load_settings(env={}, overrides={"sizing": {"mode": "MULTIPLIER"}})
    cfg = load_settings(env={}).sizing.model_copy(update={"mode": "MULTIPLIER"})
    with pytest.raises(PermissionError):
        lots_for_entry(2, cfg, get_instrument("XAUUSD"))
    s = load_settings("multiplier_high_risk", env={})
    assert any("MULTIPLIER" in w for w in s.high_risk)
    assert [lots_for_entry(k, s.sizing, get_instrument("XAUUSD")) for k in (1, 2, 3)] == [0.01, 0.02, 0.04]


def test_max_positions_has_a_hard_ceiling():
    with pytest.raises(ValueError):
        load_settings(env={}, overrides={"risk": {"max_positions": HARD_MAX_POSITIONS + 1}})


def test_every_basket_needs_a_loss_limit():
    with pytest.raises(ValueError):
        load_settings(env={}, overrides={"risk": {"max_basket_loss_percent": 0}})


def test_strategy_never_imports_a_broker_or_executor():
    for f in (APP / "strategy").glob("*.py"):
        tree = ast.parse(f.read_text())
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        bad = {m for m in mods if "providers" in m or "simulator" in m or "requests" in m or "paper" in m}
        assert not bad, f"{f.name} imports {bad}"


def test_broker_provider_has_no_order_methods():
    src = (APP / "market" / "providers" / "broker_provider.py").read_text().lower()
    for word in ("def place_order", "def submit_order", "def create_order", "post(", "put("):
        assert word not in src


def test_exposure_limit_blocks_open():
    d = Driver(risk={"max_exposure_leverage": 0.1})     # 0.01 lot of gold ~ 2000 USD notional = 0.2x
    d.bar(2000)
    d.tick(2000, gap=True)
    assert d.basket is None
    assert any(e.type == "RISK_REJECTED" and "EXPOSURE" in e.message for e in d.robot.log.entries)


def test_margin_limit_blocks_adds():
    # 1% margin on gold: each 0.01 lot ~ 20 USD margin; 0.5% of 10k = 50 USD -> at most 2 positions
    d = Driver(risk={"max_margin_usage_percent": 0.5})
    d.bar(2000)
    d.tick(2000, gap=True)
    d.path(1990.0)
    assert d.basket.n == 2
    assert "MARGIN" in d.basket.adds_blocked


def test_daily_loss_limit_blocks_new_baskets():
    d = Driver(risk={"max_daily_loss_percent": 0.3, "max_basket_loss_percent": 0.5, "cooldown_bars_after_loss": 0})
    d.bar(2000)
    d.tick(2000, gap=True)
    d.tick(1940, gap=True)              # -60 USD > 0.3% of the day's start
    assert d.basket is None
    for _ in range(4):
        d.bar(1940)
        d.tick(1940, gap=True)
    assert d.basket is None
    assert any("DAILY_LOSS" in e.message for e in d.robot.log.entries if e.type == "RISK_REJECTED")


def test_emergency_stop_preserves_positions_and_blocks_everything():
    d = Driver()
    d.bar(2000)
    d.tick(2000, gap=True)
    d.path(1997.0)
    assert d.basket.n == 2
    d.robot.emergency_stop("test")
    assert d.robot.status == "STOPPED"
    d.path(1980.0)                      # would add 3 more and nothing else
    assert d.basket is not None and d.basket.n == 2
    assert d.robot.account.floating_pnl < 0         # still marked to market
    d.path(2030.0)                      # beyond the target: no strategy processing, so no close either
    assert d.basket is not None
    d.bar(2030)
    assert d.robot.executor.pending() == []
    assert "EMERGENCY_STOP" in d.types()
    d.robot.resume()
    assert d.robot.status != "STOPPED"
    d.tick(2030)
    assert d.basket is None             # target handled after resume


def test_emergency_stop_cancels_pending_orders():
    d = Driver(execution={"delay_ms": 60_000})
    d.bar(2000)
    d.tick(2000, gap=True)              # OPEN still queued (1 min delay)
    assert d.robot.executor.pending()
    d.robot.emergency_stop()
    assert d.robot.executor.pending() == []
    d.tick(2000, seconds=120)
    assert d.basket is None
    assert not d.robot.strategy.pending_open


def test_manual_close():
    d = Driver()
    d.bar(2000)
    d.tick(2000, gap=True)
    assert d.robot.close_basket_manually()
    assert d.basket is None and d.robot.completed[-1].close_reason == "MANUAL"


def test_account_drawdown_closes_and_halts_until_resumed():
    d = Driver(risk={"max_account_drawdown_percent": 1.0, "max_basket_loss_percent": 5.0})
    d.bar(2000)
    d.tick(2000, gap=True)
    for k in range(1, 200):
        d.tick(2000 - k * 0.5)
        if d.robot.halted:
            break
    assert d.robot.halted and d.basket is None
    assert d.robot.completed[-1].close_reason == "ACCOUNT_DRAWDOWN"
    assert d.robot.status == "STOPPED"
    for _ in range(15):
        d.bar(1900)
        d.tick(1900, gap=True)
    assert d.basket is None
    d.robot.resume()
    for _ in range(15):
        d.bar(1900)
        d.tick(1900, gap=True)
    assert d.basket is not None


def test_stale_data_blocks_new_positions():
    d = Driver()
    d.robot.set_data_status(False, "no tick for 300 s", d.t)
    d.bar(2000)
    d.tick(2000, gap=True)
    assert d.basket is None
    assert "DATA_STALE" in d.types()
    d.robot.set_data_status(True, "fresh", d.t)
    d.bar(2000)
    d.tick(2000, gap=True)
    assert d.basket is not None


def test_paper_account_is_labelled_and_simulated():
    snap = Driver().robot.snapshot()
    assert snap["account"]["label"] == "PAPER ACCOUNT" and snap["account"]["simulated"] is True
    assert snap["live_trading_enabled"] is False


def test_environment_variables_map_to_settings():
    s = load_settings(env={"GRID_DISTANCE": "3.5", "GRID_MODE": "FIXED", "MAX_POSITIONS": "4", "MAX_BASKET_LOSS": "150",
                           "INTRABAR_ORDER": "RANDOM", "EXECUTION_DELAY_MS": "250", "VIDEO_STYLE_MODE": "false"})
    assert (s.grid.distance, s.grid.mode.value, s.risk.max_positions, s.risk.max_basket_loss_usd) == (3.5, "FIXED", 4, 150)
    assert s.execution.intrabar_order == "RANDOM" and s.execution.delay_ms == 250
    assert load_settings(env={"VIDEO_STYLE_MODE": "true"}).name == "video_style"


def test_env_file_loader_does_not_override_real_env(tmp_path, monkeypatch):
    from app.config import load_env_file
    f = tmp_path / ".env"
    f.write_text("# comment\nGRID_DISTANCE=7  # inline\nMAX_POSITIONS=3\nOANDA_API_TOKEN=\n")
    monkeypatch.setenv("MAX_POSITIONS", "2")
    monkeypatch.delenv("GRID_DISTANCE", raising=False)
    assert load_env_file(f) == 1
    import os
    assert os.environ["GRID_DISTANCE"] == "7" and os.environ["MAX_POSITIONS"] == "2"
    monkeypatch.delenv("GRID_DISTANCE")
