"""
Paper trading: the backtest engine run forward on new closed candles with
virtual money. Completely separate from any real execution: there is no
broker code here, and app/paper/broker.py fails closed on anything live.

Each `step`:
  1. downloads the latest candles (or takes them from the caller) and stores them;
  2. rebuilds features over recent history (warm-up included);
  3. feeds every candle that has CLOSED since the last step to the engine, in
     order: exactly what a backtest over those bars would do, no look-ahead;
  4. saves the engine state, completed baskets, risk events, signals and an
     equity snapshot to SQLite.

A new account doesn't back-fill history: its first step only analyses the
latest closed bar, so the first basket can open on the bar after that.

Engine state holds bar indices; features are rebuilt each step, so indices
are stored relative to the last processed bar and re-anchored on load.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime

import pandas as pd

from app.backtest.engine import EngineState, GridEngine
from app.backtest.runner import basket_rows, prepare_features
from app.config import Settings
from app.data.downloader import download
from app.data.repository import load_candles, save_candles
from app.data.validator import closed_candles
from app.models.database import (Basket as BasketRow, CompletedBasket, EquitySnapshot, PaperAccount, RiskEvent,
                                 StrategySignal, session_factory)
from app.risk.drawdown import DrawdownTracker
from app.risk.exposure import leverage, margin_usage_pct
from app.risk.risk_manager import RiskState
from app.strategy.basket_manager import Basket, Position
from app.utils.time import utc_now

HISTORY_BARS = 3000   # enough warm-up for EMA200 and the 500-bar volatility percentile


# ------------------------------------------------------------------------ state (de)serialisation
def _dump_state(st: EngineState, last_i: int, anchor: pd.Timestamp) -> dict:
    rel = lambda idx: None if idx is None else idx - last_i
    bk = st.basket
    basket = None
    if bk is not None:
        d = asdict(bk)
        d["opened_at"] = bk.opened_at.isoformat()
        d["open_index"], d["last_entry_index"] = rel(bk.open_index), rel(bk.last_entry_index)
        for p in d["positions"]:
            p["entry_time"] = p["entry_time"].isoformat()
        basket = d
    pending = None
    if st.pending:
        pending = {**st.pending, "execute_at": rel(st.pending["execute_at"]), "signal_index": rel(st.pending["signal_index"])}
    t = st.tracker
    return {
        "anchor": anchor.isoformat(), "balance": st.balance, "basket": basket, "pending": pending,
        "basket_counter": st.basket_counter, "last_close_index": rel(st.last_close_index),
        "risk": {"halted": st.risk.halted, "cooldown_until": rel(st.risk.cooldown_until)},
        "tracker": {"peak": t.peak, "max_drawdown_pct": t.max_drawdown_pct, "day": t.day.isoformat() if t.day else None,
                    "day_start_equity": t.day_start_equity},
        "bars_processed": st.bars_processed,
    }


def _load_state(d: dict, last_i: int) -> EngineState:
    ab = lambda off: None if off is None else last_i + off
    basket = None
    if d.get("basket"):
        b = dict(d["basket"])
        positions = [Position(**{**p, "entry_time": datetime.fromisoformat(p["entry_time"])}) for p in b.pop("positions")]
        b["opened_at"] = datetime.fromisoformat(b["opened_at"])
        b["open_index"], b["last_entry_index"] = ab(b["open_index"]), ab(b["last_entry_index"])
        basket = Basket(**b, positions=positions)
    pending = None
    if d.get("pending"):
        p = d["pending"]
        pending = {**p, "execute_at": ab(p["execute_at"]), "signal_index": ab(p["signal_index"])}
    t = d["tracker"]
    tracker = DrawdownTracker(peak=t["peak"], max_drawdown_pct=t["max_drawdown_pct"],
                              day=date.fromisoformat(t["day"]) if t["day"] else None, day_start_equity=t["day_start_equity"])
    return EngineState(balance=d["balance"], basket=basket, pending=pending, basket_counter=d["basket_counter"],
                       last_close_index=ab(d["last_close_index"]),
                       risk=RiskState(halted=d["risk"]["halted"], cooldown_until=ab(d["risk"]["cooldown_until"])),
                       tracker=tracker, bars_processed=d.get("bars_processed", 0))


# ------------------------------------------------------------------------------------ accounts
def create_account(name: str, settings: Settings, db=None) -> PaperAccount:
    if not settings.paper_trading:
        raise ValueError("paper_trading is off in this configuration")
    Session = session_factory(db)
    with Session() as s, s.begin():
        if s.query(PaperAccount).filter_by(name=name).one_or_none():
            raise ValueError(f"paper account {name!r} already exists")
        cap = settings.risk.initial_capital
        acct = PaperAccount(name=name, symbol=settings.market.symbol, timeframe=settings.market.timeframe,
                            config=settings.model_dump(mode="json"), state={}, balance=cap, equity=cap)
        s.add(acct)
    return acct


def get_account(name: str, db=None) -> PaperAccount:
    with session_factory(db)() as s:
        acct = s.query(PaperAccount).filter_by(name=name).one_or_none()
        if acct is None:
            raise KeyError(f"no paper account named {name!r}")
        return acct


def step(name: str, candles: pd.DataFrame | None = None, now: datetime | None = None, db=None) -> dict:
    """Process every newly closed candle for the account. Returns what happened."""
    acct = get_account(name, db)
    settings = Settings.model_validate(acct.config)   # re-validated: live trading stays refused
    sym, tf = acct.symbol, acct.timeframe
    if candles is None:
        fresh, info = download(sym, tf)
        save_candles(fresh, sym, tf, info["source"], db)
        candles = load_candles(sym, tf, db=db)
    candles = closed_candles(candles, tf, now).tail(HISTORY_BARS).reset_index(drop=True)
    if len(candles) < 2:
        raise ValueError("not enough closed candles")
    f = prepare_features(candles, settings)
    ts = f["timestamp"]

    if not acct.state:                                   # first step: analyse the latest bar only
        first_i = len(f) - 1
        eng = GridEngine(settings)
        eng.state.tracker = DrawdownTracker(peak=settings.risk.initial_capital)
    else:
        anchor = pd.Timestamp(acct.state["anchor"])
        pos = int(ts.searchsorted(anchor))
        if pos >= len(ts) or ts.iloc[pos] != anchor:
            raise ValueError("stored history no longer contains the last processed bar; state unchanged")
        eng = GridEngine(settings, state=_load_state(acct.state, pos))
        first_i = pos + 1
    n_done, n_sig, n_evt = len(eng.state.completed), len(eng.state.signals), len(eng.state.risk_events)
    for i in range(max(first_i, 1), len(f)):
        eng.process_bar(f, i)
    last_i = len(f) - 1
    st = eng.state
    new_baskets, new_signals, new_events = st.completed[n_done:], st.signals[n_sig:], st.risk_events[n_evt:]

    row = f.iloc[-1]
    equity = eng._equity(st.balance, st.basket, row["close"], row, row["open"])
    Session = session_factory(db)
    with Session() as s, s.begin():
        a = s.query(PaperAccount).filter_by(name=name).one()
        a.state = _dump_state(st, last_i, ts.iloc[-1])
        a.balance, a.equity, a.halted = st.balance, equity, eng.risk.state.halted
        a.last_bar, a.updated_at = ts.iloc[-1].to_pydatetime(), utc_now()
        for b in new_baskets:
            s.add_all(basket_rows(b, paper_account_id=a.id))
            s.query(BasketRow).filter_by(paper_account_id=a.id, basket_uid=b.uid).update({"status": "CLOSED"})
        if st.basket is not None:
            bk = st.basket
            existing = s.query(BasketRow).filter_by(paper_account_id=a.id, basket_uid=bk.uid).one_or_none()
            vals = dict(direction=bk.direction, status="OPEN", opened_at=bk.opened_at, positions=bk.n,
                        total_lots=bk.total_lots, avg_entry=bk.avg_entry, updated_at=utc_now())
            if existing:
                for k, v in vals.items():
                    setattr(existing, k, v)
            else:
                s.add(BasketRow(paper_account_id=a.id, basket_uid=bk.uid, **vals))
        for e in new_events:
            s.add(RiskEvent(paper_account_id=a.id, ts=pd.Timestamp(e["ts"]).to_pydatetime(), kind=e["kind"], detail=e["detail"]))
        for g in new_signals:
            s.add(StrategySignal(paper_account_id=a.id, ts=pd.Timestamp(g["ts"]).to_pydatetime(), signal=g["signal"],
                                 regime=g["regime"], vol_regime=g["vol_regime"], acted=g["acted"], reason=g["reason"]))
        for p in st.curve:
            s.add(EquitySnapshot(paper_account_id=a.id, ts=p.ts, balance=p.balance, equity=p.equity,
                                 drawdown_pct=p.drawdown_pct, open_positions=p.positions))
    return {"account": name, "bars_processed": len(st.curve), "last_bar": ts.iloc[-1].isoformat(),
            "balance": st.balance, "equity": equity, "halted": eng.risk.state.halted,
            "closed_baskets": [b.to_dict() for b in new_baskets], "risk_events": new_events,
            "open_basket": st.basket.uid if st.basket else None}


def resume(name: str, db=None) -> str:
    """Lift an account-drawdown halt. A deliberate human decision; never automatic."""
    Session = session_factory(db)
    with Session() as s, s.begin():
        a = s.query(PaperAccount).filter_by(name=name).one()
        if not a.state or not a.state["risk"]["halted"]:
            return "not halted"
        state = dict(a.state)
        state["risk"] = {**state["risk"], "halted": None}
        state["tracker"] = {**state["tracker"], "peak": a.equity}   # drawdown is measured from here on
        a.state, a.halted = state, None
        s.add(RiskEvent(paper_account_id=a.id, ts=utc_now(), kind="RESUMED", detail="halt lifted by the user"))
    return "resumed"


def status(name: str, db=None) -> dict:
    """Virtual balance, equity, open basket, drawdown, history and risk status."""
    Session = session_factory(db)
    with Session() as s:
        a = s.query(PaperAccount).filter_by(name=name).one()
        settings = Settings.model_validate(a.config)
        from app.data.instruments import get_instrument

        inst = get_instrument(a.symbol)
        closed = s.query(CompletedBasket).filter_by(paper_account_id=a.id).order_by(CompletedBasket.closed_at.desc()).all()
        events = s.query(RiskEvent).filter_by(paper_account_id=a.id).order_by(RiskEvent.ts.desc()).limit(50).all()
        curve = s.query(EquitySnapshot).filter_by(paper_account_id=a.id).order_by(EquitySnapshot.ts).all()
        basket = None
        if a.state and a.state.get("basket"):
            b = a.state["basket"]
            lots = sum(p["lots"] for p in b["positions"])
            avg = sum(p["lots"] * p["entry_price"] for p in b["positions"]) / lots
            basket = {"basket_id": b["uid"], "direction": b["direction"], "positions": len(b["positions"]),
                      "total_lots": lots, "average_entry": avg, "opened_at": b["opened_at"],
                      "loss_limit": b["loss_limit_usd"], "target_usd": b["target_usd"], "target_distance": b["target_distance"],
                      "mae": b["mae"], "mfe": b["mfe"], "entries": b["positions"]}
        last_close = None
        if curve:
            last_close = curve[-1]
        peak = max([c.equity for c in curve] + [settings.risk.initial_capital])
        dd = (peak - a.equity) / peak * 100 if peak else 0.0
        return {
            "name": a.name, "symbol": a.symbol, "timeframe": a.timeframe, "strategy": settings.name,
            "starting_balance": settings.risk.initial_capital, "balance": a.balance, "equity": a.equity,
            "pnl": a.equity - settings.risk.initial_capital, "drawdown_pct": dd, "halted": a.halted,
            "last_bar": a.last_bar.isoformat() if a.last_bar else None, "open_basket": basket,
            "exposure_leverage": leverage(basket["total_lots"], basket["average_entry"], a.equity, inst) if basket else 0.0,
            "margin_usage_pct": margin_usage_pct(basket["total_lots"], basket["average_entry"], a.equity, inst) if basket else 0.0,
            "baskets": [{"uid": c.basket_uid, "direction": c.direction, "opened_at": c.opened_at.isoformat(),
                         "closed_at": c.closed_at.isoformat(), "positions": c.positions, "pnl": c.pnl, "mae": c.mae,
                         "close_reason": c.close_reason} for c in closed[:100]],
            "risk_events": [{"ts": e.ts.isoformat(), "kind": e.kind, "detail": e.detail} for e in events],
            "equity_curve": [{"t": c.ts.isoformat(), "equity": c.equity, "balance": c.balance} for c in curve[-2000:]],
            "last_equity_point": last_close.ts.isoformat() if last_close else None,
            "limits": settings.risk.model_dump(),
        }


def list_accounts(db=None) -> list[dict]:
    with session_factory(db)() as s:
        return [{"name": a.name, "symbol": a.symbol, "timeframe": a.timeframe, "balance": a.balance, "equity": a.equity,
                 "halted": a.halted, "last_bar": a.last_bar.isoformat() if a.last_bar else None,
                 "strategy": a.config.get("name")} for a in s.query(PaperAccount).order_by(PaperAccount.name)]
