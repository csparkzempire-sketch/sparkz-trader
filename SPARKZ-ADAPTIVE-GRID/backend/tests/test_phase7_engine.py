"""
Phase 7: the event-driven engine, on hand-built price paths where the right
answer can be worked out by hand.

Features are forced so that exactly the bars we choose produce a BUY or SELL
signal (regime UNCERTAIN everywhere else), with a constant ATR.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.backtest.engine import GridEngine
from app.backtest.runner import prepare_features, run_backtest
from app.config import load_config
from tests.conftest import make_candles

SPREAD, SLIP = 0.30, 0.05          # gold defaults: half spread + slippage = 0.20 per fill


def cfg(**env):
    base = {"COOLDOWN_BARS_AFTER_LOSS": "0", "MAX_DAILY_LOSS_PERCENT": "100", "MAX_ACCOUNT_DRAWDOWN_PERCENT": "99",
            "GRID_MODE": "PRICE", "GRID_PRICE_STEP": "1.0", "BASKET_TARGET_MODE": "FIXED", "BASKET_TARGET_USD": "10",
            "MAX_BASKET_LOSS_PERCENT": "1.0"}
    base.update({k: str(v) for k, v in env.items()})
    return load_config(env=base)


def frame(ohlc: list[tuple[float, float, float, float]], signals: dict[int, str], atr: float = 2.0) -> pd.DataFrame:
    """Bars from (open, high, low, close) tuples; `signals` maps bar index -> 'BUY'/'SELL' at that bar's close."""
    ts = pd.date_range("2026-01-05 00:00", periods=len(ohlc), freq="15min", tz="UTC")
    df = pd.DataFrame(ohlc, columns=["open", "high", "low", "close"])
    df.insert(0, "timestamp", ts)
    df["volume"] = 1.0
    for col, v in {"ema_trend": 0.0, "atr": atr, "vol_percentile": 0.5, "bb_mid": 0.0, "bb_upper": 1e9, "bb_lower": 0.0,
                   "adx": 10.0, "rsi": 50.0, "plus_di": 20.0, "minus_di": 20.0}.items():
        df[col] = v
    df["ema_fast"] = df["close"]
    df["ema_slow"] = df["close"]
    df["regime"] = "UNCERTAIN"
    df["vol_regime"] = "NORMAL_VOLATILITY"
    for i, d in signals.items():
        c = df.at[i, "close"]
        up = d == "BUY"
        df.loc[i, ["regime", "adx", "rsi"]] = ["TRENDING_UP" if up else "TRENDING_DOWN", 30.0, 60.0 if up else 40.0]
        df.loc[i, ["ema_fast", "ema_slow"]] = [c - 1, c - 2] if up else [c + 1, c + 2]
    return df


def run(f, settings):
    eng = GridEngine(settings)
    for i in range(1, len(f)):
        eng.process_bar(f, i)
    eng.finish(f)
    return eng


def flat(n, p=2350.0):
    return [(p, p + 0.1, p - 0.1, p)] * n


# --- timing: no look-ahead -----------------------------------------------------------------------

def test_signal_fills_at_the_next_bars_open():
    bars = flat(3) + [(2351.0, 2351.1, 2350.9, 2351.0)] + flat(3, 2351.0)
    eng = run(frame(bars, {2: "BUY"}), cfg())
    b = eng.state.completed[0]
    assert b.opened_at == pd.Timestamp("2026-01-05 00:45", tz="UTC")               # bar 3 = signal bar 2 + 1
    assert b.entries[0]["price"] == pytest.approx(2351.0 + SPREAD / 2 + SLIP)      # bar 3's open, plus costs


def test_entry_latency_delays_the_fill():
    bars = flat(8)
    eng = run(frame(bars, {2: "BUY"}), cfg(ENTRY_LATENCY_BARS=2))
    assert eng.state.completed[0].opened_at == pd.Timestamp("2026-01-05 01:15", tz="UTC")   # bar 5


def test_future_bars_do_not_change_past_decisions():
    df = make_candles(1200, drift=0.02, vol=1.5, seed=4)
    s = cfg(GRID_MODE="ATR", BASKET_TARGET_MODE="PERCENT", COOLDOWN_BARS_AFTER_LOSS=4)
    full = prepare_features(df, s)
    cut = 900
    part = prepare_features(df.iloc[:cut], s)
    a, b = run(full, s), run(part, s)
    cutoff = full["timestamp"].iloc[cut - 2]
    done_a = [x.to_dict() for x in a.state.completed if x.closed_at < cutoff]
    done_b = [x.to_dict() for x in b.state.completed if x.closed_at < cutoff]
    assert done_a and done_a == done_b


# --- targets, stops, gaps ------------------------------------------------------------------------

def test_basket_closes_exactly_at_its_target():
    # BUY 0.01 lot (1 oz) at 2350.20; +$10 needs an exit (bid) of 2360.20 -> mid 2360.40
    bars = flat(3) + [(2350.0, 2350.1, 2349.9, 2350.0), (2350.0, 2365.0, 2349.9, 2364.0)] + flat(2, 2364.0)
    eng = run(frame(bars, {2: "BUY"}, atr=100.0), cfg(GRID_MODE="NONE"))
    b = eng.state.completed[0]
    assert b.close_reason == "TARGET" and b.pnl == pytest.approx(10.0) and b.exit_price == pytest.approx(2360.20)
    assert eng.state.balance == pytest.approx(10_010.0)


def test_basket_stop_caps_the_loss():
    # 1% of 10,000 = $100 = 100 price units on 1 oz. Entry 2350.20 -> exit (bid) 2250.20 -> mid 2250.40
    bars = flat(3) + [(2350.0, 2350.1, 2200.0, 2210.0)] + flat(2, 2210.0)
    eng = run(frame(bars, {2: "BUY"}, atr=500.0), cfg(GRID_MODE="NONE"))
    b = eng.state.completed[0]
    assert b.close_reason == "BASKET_STOP" and b.pnl == pytest.approx(-100.0)


def test_a_gap_through_the_stop_fills_at_the_worse_open():
    bars = flat(3) + [(2350.0, 2350.1, 2349.9, 2350.0), (2200.0, 2201.0, 2190.0, 2195.0)] + flat(2, 2195.0)
    eng = run(frame(bars, {2: "BUY"}, atr=500.0), cfg(GRID_MODE="NONE"))
    b = eng.state.completed[0]
    assert b.close_reason == "BASKET_STOP"
    assert b.exit_price == pytest.approx(2200.0 - SPREAD / 2 - SLIP) and b.pnl < -100.0


def test_open_basket_is_closed_at_the_end_of_data():
    eng = run(frame(flat(6), {2: "SELL"}), cfg())
    assert eng.state.completed[-1].close_reason == "END_OF_DATA" and eng.state.basket is None


# --- grid adds and the position cap --------------------------------------------------------------

def _falling(n, start=2350.0, step=1.2):
    out, p = [], start
    for _ in range(n):
        out.append((p, p + 0.05, p - step, p - step))
        p -= step
    return out


def _sawtooth(n, start=2350.0):
    """Drifts down 0.5 a bar but each bar opens above the next grid level, so adds fill exactly at the level."""
    out, p = [], start
    for _ in range(n):
        out.append((p, p + 0.05, p - 1.5, p - 0.5))
        p -= 0.5
    return out


def test_averaging_grid_adds_at_each_level_and_never_exceeds_max_positions():
    bars = flat(3) + _sawtooth(40)
    s = cfg(MAX_POSITIONS=4, MAX_BASKET_LOSS_PERCENT=50)
    eng = run(frame(bars, {2: "BUY"}), s)
    b = eng.state.completed[0]
    assert b.positions == 4
    refs = [e["price"] - SPREAD / 2 - SLIP for e in b.entries]
    assert [round(r2 - r1, 6) for r1, r2 in zip(refs, refs[1:])] == [-1.0, -1.0, -1.0]   # 1.00 grid
    assert any(e["kind"] == "POSITION_LIMIT" for e in eng.state.risk_events)


def test_an_add_that_became_due_while_adds_were_paused_fills_at_the_next_open():
    """Price fell through the level on the entry bar, when no add is allowed (one entry per bar):
    the add fills at the next bar's open, the price actually available then."""
    bars = flat(3) + _falling(3)
    b = run(frame(bars, {2: "BUY"}), cfg(MAX_BASKET_LOSS_PERCENT=50)).state.completed[0]
    assert b.entries[1]["price"] - SPREAD / 2 - SLIP == pytest.approx(2350.0 - 1.2)


@pytest.mark.parametrize("sizing,extra", [("FIXED", {}), ("LINEAR", {}),
                                          ("MARTINGALE", {"ALLOW_MARTINGALE": "true"})])
def test_no_configuration_opens_unlimited_positions(sizing, extra):
    """A long one-way move against the basket, tiny spacing, several adds allowed per bar, a loss limit
    and leverage cap that never bind: the basket still stops at max_positions."""
    bars = flat(3) + _falling(300, step=3.0)
    s = cfg(MAX_POSITIONS=20, POSITION_SIZING=sizing, GRID_PRICE_STEP=0.5, MAX_BASKET_LOSS_PERCENT=100,
            MAX_EXPOSURE_LEVERAGE=1e6, MAX_MARGIN_USAGE_PERCENT=1e6, INITIAL_CAPITAL=1e9, **extra)
    s.grid.min_bars_between_entries = 0
    eng = run(frame(bars, {2: "BUY"}), s)
    assert max(len(b.entries) for b in eng.state.completed) <= 20
    for pt in eng.state.curve:
        assert pt.positions <= 20


def test_martingale_sizes_grow_geometrically_inside_a_basket():
    bars = flat(3) + _falling(10)
    eng = run(frame(bars, {2: "BUY"}), cfg(POSITION_SIZING="MARTINGALE", ALLOW_MARTINGALE="true", MAX_POSITIONS=4,
                                          MAX_BASKET_LOSS_PERCENT=50))
    assert [e["lots"] for e in eng.state.completed[0].entries] == pytest.approx([0.01, 0.02, 0.04, 0.08])


def test_pyramiding_adds_only_while_the_basket_is_winning():
    rising = [(2350.0 + k, 2351.2 + k, 2349.95 + k, 2351.0 + k) for k in range(12)]
    s = cfg(POSITION_SIZING="PYRAMID", BASKET_TARGET_USD="1000", MAX_BASKET_LOSS_PERCENT=50)
    eng = run(frame(flat(3) + rising, {2: "BUY"}), s)
    b = eng.state.completed[0]
    prices = [e["price"] for e in b.entries]
    assert len(prices) > 1 and prices == sorted(prices)           # each add higher than the last
    down = run(frame(flat(3) + _falling(12), {2: "BUY"}), s)
    assert down.state.completed[0].positions == 1                 # never averages down


def test_signal_confirmed_grid_stops_adding_when_the_trend_turns():
    bars = flat(3) + _falling(20)
    s = cfg(GRID_MODE="SIGNAL_CONFIRMED", GRID_ATR_MULTIPLIER=0.5, MAX_BASKET_LOSS_PERCENT=50, MAX_POSITIONS=10)
    f = frame(bars, {2: "BUY"}, atr=2.0)
    f.loc[3:, "ema_fast"] = f.loc[3:, "close"] - 1      # BUY still backed: EMA20 > EMA50
    f.loc[3:, "ema_slow"] = f.loc[3:, "close"] - 2
    backed = run(f, s).state.completed[0].positions
    f.loc[6:, "ema_fast"] = f.loc[6:, "close"] - 2       # from bar 6: EMA20 < EMA50
    f.loc[6:, "ema_slow"] = f.loc[6:, "close"] - 1
    turned = run(f, s).state.completed[0].positions
    assert backed == 10 and turned < backed


# --- reset, cooldown, sizing after losses -------------------------------------------------------

def test_new_basket_needs_a_fresh_analysis_after_a_close():
    bars = flat(3) + [(2350.0, 2365.0, 2349.9, 2364.0)] + flat(6, 2364.0)
    sig = {k: "BUY" for k in range(2, 9)}              # the signal is "on" every bar
    eng = run(frame(bars, sig, atr=100.0), cfg(GRID_MODE="NONE"))
    first, second = eng.state.completed[:2]
    assert first.closed_at == pd.Timestamp("2026-01-05 00:45", tz="UTC")       # closed on bar 3
    assert second.opened_at == pd.Timestamp("2026-01-05 01:15", tz="UTC")      # signal bar 4 -> bar 5, not bar 4


def test_cooldown_after_a_losing_basket():
    bars = flat(3) + [(2350.0, 2350.1, 2200.0, 2210.0)] + flat(20, 2210.0)
    sig = {k: "BUY" for k in range(2, 22)}
    eng = run(frame(bars, sig, atr=500.0), cfg(GRID_MODE="NONE", COOLDOWN_BARS_AFTER_LOSS=8))
    loss, nxt = eng.state.completed[:2]
    assert loss.pnl < 0
    assert (nxt.opened_at - loss.closed_at) >= pd.Timedelta(minutes=15 * 9)


def test_a_loss_never_makes_the_next_basket_bigger():
    bars = flat(3) + [(2350.0, 2350.1, 2200.0, 2210.0)] + flat(6, 2210.0) + [(2210.0, 2230.0, 2209.9, 2229.0)] + flat(3, 2229.0)
    sig = {k: "BUY" for k in range(2, 12)}
    eng = run(frame(bars, sig, atr=500.0), cfg(GRID_MODE="NONE", POSITION_SIZING="LINEAR"))
    assert {b.entries[0]["lots"] for b in eng.state.completed} == {0.01}


# --- account-level risk ---------------------------------------------------------------------------

def test_account_drawdown_closes_and_halts_for_good():
    bars = flat(3) + [(2350.0, 2350.1, 2330.0, 2331.0)] + flat(10, 2331.0)
    sig = {k: "BUY" for k in range(2, 12)}
    s = cfg(GRID_MODE="NONE", MAX_ACCOUNT_DRAWDOWN_PERCENT=0.1, MAX_BASKET_LOSS_PERCENT=5, BASE_LOT=0.1)
    eng = run(frame(bars, sig, atr=500.0), s)
    assert eng.state.completed[0].close_reason == "ACCOUNT_DRAWDOWN"
    assert len(eng.state.completed) == 1 and eng.risk.state.halted
    assert any(e["kind"] == "HALTED" for e in eng.state.risk_events)


def test_daily_loss_limit_blocks_new_baskets_until_tomorrow():
    bars = flat(3) + [(2350.0, 2350.1, 2200.0, 2210.0)] + flat(12, 2210.0)
    sig = {k: "BUY" for k in range(2, 15)}
    s = cfg(GRID_MODE="NONE", MAX_DAILY_LOSS_PERCENT=0.5)
    eng = run(frame(bars, sig, atr=500.0), s)
    assert len(eng.state.completed) == 1
    assert any(e["kind"] == "DAILY_LOSS" for e in eng.state.risk_events)


# --- intrabar ordering -----------------------------------------------------------------------------

def test_pessimistic_mode_never_books_the_better_of_two_orders():
    """One bar reaches both the next grid level (low) and the target with the add included (high).
    Adverse-first would add then take profit; favourable-first would take the smaller profit or none.
    PESSIMISTIC must keep the outcome with less equity at the close."""
    bars = flat(3) + [(2350.0, 2350.1, 2349.9, 2350.0), (2350.0, 2358.5, 2348.5, 2352.0)] + flat(2, 2352.0)
    f = frame(bars, {2: "BUY"})
    pess = run(f, cfg())
    path = run(f, cfg(INTRABAR_MODE="OHLC_PATH"))
    assert pess.state.balance <= path.state.balance


def test_equity_curve_has_one_point_per_bar():
    df = make_candles(700, drift=0.05, seed=2)
    res = run_backtest(cfg(GRID_MODE="ATR"), candles=df)
    assert len(res.curve) == len(df) - 1 and res.metrics["bars"] == len(df) - 1


def test_backtests_are_saved_to_sqlite():
    from app.models.database import Backtest, CompletedBasket, session_factory

    res = run_backtest(cfg(GRID_MODE="ATR", BASKET_TARGET_MODE="PERCENT"), candles=make_candles(900, seed=5), save=True)
    with session_factory()() as s:
        bt = s.get(Backtest, res.backtest_id)
        assert bt.summary["metrics"]["baskets"] == len(res.baskets)
        assert s.query(CompletedBasket).filter_by(backtest_id=bt.id).count() == len(res.baskets)


def test_atr_normalized_sizing_is_fixed_per_basket_from_the_signal_bar():
    bars = flat(3) + _sawtooth(10)
    s = cfg(BASE_LOT_MODE="ATR_NORMALIZED", USD_PER_ATR="20", MAX_BASKET_LOSS_PERCENT=50)
    b = run(frame(bars, {2: "BUY"}, atr=2.0), s).state.completed[0]
    # $20 per ATR, ATR 2.0, gold 100 oz per lot -> 0.10 lot, and every add uses that base
    assert len(b.entries) > 1 and [e["lots"] for e in b.entries] == pytest.approx([0.10] * len(b.entries))
