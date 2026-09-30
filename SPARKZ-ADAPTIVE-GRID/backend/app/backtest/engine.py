"""
Event-driven grid & basket engine. The backtester and the paper simulator
both drive this same class, bar by bar, so they can't disagree.

The cycle (one basket at a time):

  analyse bar N's close -> signal? -> [latency] -> open basket at the next bar's open
  -> every bar: walk the price path, adding grid positions / hitting the basket
     target / hitting the basket stop, in the order price reaches them
  -> basket closes -> record it -> re-analyse from the NEXT bar (never the bar it closed on)

No look-ahead:
- entry signals use bar N's features and fill at bar N+1+latency's OPEN;
- grid spacing uses the ATR of the last closed bar; the signal-confirmed grid
  asks the analysis of the last closed bar;
- intrabar events only use the bar's own open/high/low/close, in a path order
  that is decided before looking at which order would be better.

Intrabar order (execution.intrabar_mode):
- PESSIMISTIC (default): a 15-minute bar may have gone either way first. Both
  orders (adverse extreme first, favourable extreme first) are simulated and
  the one that leaves LESS equity at the bar's close is kept. A grid can't
  "add at the low and take profit at the high" of the same bar unless that is
  also the worse of the two possibilities.
- OHLC_PATH: open -> low -> high -> close on up bars, open -> high -> low ->
  close on down bars. Offered for sensitivity checks.

Fills: entries and adds at the trigger mid (or at the open when the bar gapped
through the level), plus half the spread and slippage. Stops and forced exits
likewise, gaps filled at the worse price. Targets fill at the target level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.backtest.execution import ExecutionModel
from app.backtest.portfolio import EquityPoint
from app.config import GridMode, Settings
from app.data.instruments import Instrument, get_instrument
from app.risk.drawdown import DrawdownTracker
from app.risk.exposure import margin, notional
from app.risk.risk_manager import RiskManager, RiskState
from app.strategy.basket_manager import Basket, CompletedBasketRecord, Position, basket_limits
from app.strategy.entry_engine import evaluate_entry, still_supports
from app.strategy.grid_engine import next_add_level
from app.strategy.market_analysis import ready
from app.strategy.position_sizing import base_lot, lot_for_entry


@dataclass
class EngineState:
    balance: float
    basket: Basket | None = None
    pending: dict | None = None          # {"direction", "signal_index", "execute_at", ...}
    basket_counter: int = 0
    last_close_index: int = -1           # bar on which the last basket closed
    risk: RiskState = field(default_factory=RiskState)
    tracker: DrawdownTracker | None = None
    completed: list[CompletedBasketRecord] = field(default_factory=list)
    curve: list[EquityPoint] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)
    risk_events: list[dict] = field(default_factory=list)
    bars_processed: int = 0


@dataclass
class _Branch:
    balance: float
    basket: Basket | None
    closed: CompletedBasketRecord | None = None
    halt: str | None = None
    events: list[dict] = field(default_factory=list)


class GridEngine:
    def __init__(self, settings: Settings, inst: Instrument | None = None, state: EngineState | None = None):
        self.s = settings
        self.inst = inst or get_instrument(settings.market.symbol)
        self.ex = ExecutionModel(self.inst, settings.execution)
        self.state = state or EngineState(balance=settings.risk.initial_capital)
        if self.state.tracker is None:
            self.state.tracker = DrawdownTracker(peak=settings.risk.initial_capital)
        self.risk = RiskManager(settings, self.inst, self.state.risk, self.state.tracker)
        self.comm = settings.execution.commission_per_lot_side

    # ----------------------------------------------------------------- helpers
    def _equity(self, balance: float, basket: Basket | None, mid: float, row, fx: float) -> float:
        if basket is None:
            return balance
        return balance + basket.pnl(self.ex.exit_fill(basket.direction, mid, row), self.inst, fx, self.comm)

    def _event(self, events: list, ts: datetime, kind: str, detail: str) -> None:
        events.append({"ts": ts.isoformat(), "kind": kind, "detail": detail})

    def _close(self, br: _Branch, mid: float, ts: datetime, i: int, reason: str, row, fx: float,
               exit_price: float | None = None) -> None:
        bk = br.basket
        px = exit_price if exit_price is not None else self.ex.exit_fill(bk.direction, mid, row)
        pnl = bk.pnl(px, self.inst, fx, self.comm)
        bk.mae, bk.mfe = min(bk.mae, pnl), max(bk.mfe, pnl)
        br.balance += pnl
        br.closed = CompletedBasketRecord(
            uid=bk.uid, direction=bk.direction, opened_at=bk.opened_at, closed_at=ts, positions=bk.n,
            total_lots=bk.total_lots, avg_entry=bk.avg_entry, exit_price=px, pnl=pnl,
            pnl_pct=pnl / bk.start_equity * 100, mae=bk.mae, mfe=bk.mfe, bars_held=i - bk.open_index + 1,
            regime=bk.regime, vol_regime=bk.vol_regime, close_reason=reason, max_notional=bk.max_notional,
            max_margin=bk.max_margin, start_equity=bk.start_equity,
            entries=[{"seq": p.seq, "lots": p.lots, "price": p.entry_price, "time": p.entry_time.isoformat()}
                     for p in bk.positions],
        )
        br.basket = None

    def _track_extreme(self, bk: Basket, mid: float, row, fx: float) -> None:
        pnl = bk.pnl(self.ex.exit_fill(bk.direction, mid, row), self.inst, fx, self.comm)
        bk.mae, bk.mfe = min(bk.mae, pnl), max(bk.mfe, pnl)

    def _add(self, br: _Branch, mid: float, ts: datetime, i: int, row, fx: float) -> bool:
        bk = br.basket
        lots = lot_for_entry(bk.n + 1, self.s.sizing, self.inst, bk.base_lot)
        equity = self._equity(br.balance, bk, mid, row, fx)
        d = self.risk.check_add(bk.n, bk.total_lots, lots, mid, equity)
        if not d.allowed:
            if bk.adds_blocked != d.code:
                self._event(br.events, ts, d.code, f"{bk.uid}: add refused, {d.detail}")
            bk.adds_blocked = d.code
            return False
        fill = self.ex.entry_fill(bk.direction, mid, row)
        bk.positions.append(Position(bk.n + 1, bk.direction, lots, ts, fill, mid, self.comm * lots))
        bk.last_entry_index, bk.adds_blocked = i, None
        bk.max_notional = max(bk.max_notional, notional(bk.total_lots, mid, self.inst))
        bk.max_margin = max(bk.max_margin, margin(bk.total_lots, mid, self.inst))
        return True

    # ------------------------------------------------------------ price path
    def _walk_leg(self, br: _Branch, a: float, b: float, ctx: dict) -> None:
        """Move price from a to b, firing adds / stop / target / account halt in the order reached."""
        ts, i, row, prev, fx = ctx["ts"], ctx["i"], ctx["row"], ctx["prev"], ctx["fx"]
        cur = a
        for _ in range(4 * self.risk.max_positions + 8):   # bounded: every loop either fires an event or exits
            bk = br.basket
            if bk is None:
                return
            sgn = bk.sign
            x = lambda p: -sgn * p          # "adverse coordinate": grows as price moves against the basket
            xa, xb = x(cur), x(b)
            events = []   # (distance from cur, priority, kind, mid level)

            stop_mid = self.ex.exit_mid_for(bk.direction, bk.stop_exit_price(self.inst, fx, self.comm), row)
            events.append(("adverse", 0, "BASKET_STOP", stop_mid))
            dd_limit = self.risk.dd.peak * (1 - self.s.risk.max_account_drawdown_percent / 100)
            dd_exit = bk.exit_price_for_pnl(dd_limit - br.balance, self.inst, fx, self.comm)
            events.append(("adverse", 1, "ACCOUNT_DRAWDOWN", self.ex.exit_mid_for(bk.direction, dd_exit, row)))
            tgt_mid = self.ex.exit_mid_for(bk.direction, bk.target_exit_price(self.inst, fx, self.comm), row)
            events.append(("favourable", 2, "TARGET", tgt_mid))

            can_add = (self.s.grid.mode != GridMode.NONE and bk.adds_blocked is None
                       and i - bk.last_entry_index >= self.s.grid.min_bars_between_entries
                       and (self.s.grid.mode != GridMode.SIGNAL_CONFIRMED or still_supports(prev, bk.direction, self.s.entry)))
            if can_add:
                lvl = next_add_level(bk.direction, bk.last_ref_price, bk.n, prev["atr"], self.s.grid, self.s.sizing.mode)
                if lvl is not None:
                    events.append(("adverse" if lvl.adverse else "favourable", 3, "ADD", lvl.price))

            hits = []
            for side, prio, kind, level in events:
                xl = x(level)
                if side == "adverse":
                    if xa >= xl:
                        hits.append((0.0, prio, kind, cur))              # already through it (gap)
                    elif xb >= xl:
                        hits.append((xl - xa, prio, kind, level))
                else:
                    if xa <= xl:
                        hits.append((0.0, prio, kind, level if kind == "TARGET" else cur))
                    elif xb <= xl:
                        hits.append((xa - xl, prio, kind, level))
            if not hits:
                return
            _, _, kind, level = min(hits)       # nearest first; ties: stop, account halt, target, add
            if kind == "ADD":
                if not self._add(br, level, ts, i, row, fx):
                    continue                    # refused: adds_blocked is set, look again without the add
                cur = level
                continue
            if kind == "TARGET":
                self._close(br, level, ts, i, "TARGET", row, fx)
            elif kind == "BASKET_STOP":
                self._close(br, level, ts, i, "BASKET_STOP", row, fx)
            else:
                self._close(br, level, ts, i, "ACCOUNT_DRAWDOWN", row, fx)
                br.halt = f"{ts:%Y-%m-%d %H:%M} UTC: account drawdown reached {self.s.risk.max_account_drawdown_percent}%"
            return
        raise RuntimeError("intrabar event loop did not settle")   # pragma: no cover - guarded by the bound

    def _run_path(self, basket: Basket, balance: float, path: list[float], ctx: dict) -> _Branch:
        br = _Branch(balance=balance, basket=basket.clone())
        for a, b in zip(path[:-1], path[1:]):
            if br.basket is None:
                break
            self._walk_leg(br, a, b, ctx)
            if br.basket is not None:
                self._track_extreme(br.basket, b, ctx["row"], ctx["fx"])
        return br

    def _intrabar(self, i: int, row, prev, ts: datetime) -> None:
        st = self.state
        o, h, l, c = row["open"], row["high"], row["low"], row["close"]
        ctx = {"ts": ts, "i": i, "row": row, "prev": prev, "fx": o}
        self._track_extreme(st.basket, o, row, o)
        if self.s.execution.intrabar_mode.value == "OHLC_PATH":
            path = [o, l, h, c] if c >= o else [o, h, l, c]
            chosen = self._run_path(st.basket, st.balance, path, ctx)
        else:
            adverse_first = [o, l, h, c] if st.basket.direction == "BUY" else [o, h, l, c]
            favourable_first = [o, h, l, c] if st.basket.direction == "BUY" else [o, l, h, c]
            branches = [self._run_path(st.basket, st.balance, p, ctx) for p in (adverse_first, favourable_first)]
            chosen = min(branches, key=lambda br: self._equity(br.balance, br.basket, c, row, o))
        st.balance, st.basket = chosen.balance, chosen.basket
        st.risk_events.extend(chosen.events)
        if chosen.closed is not None:
            self._on_closed(chosen.closed, i)
        if chosen.halt:
            self.risk.halt(chosen.halt)
            self._event(st.risk_events, ts, "HALTED", chosen.halt)

    def _on_closed(self, rec: CompletedBasketRecord, i: int) -> None:
        self.state.completed.append(rec)
        self.state.last_close_index = i
        self.risk.on_basket_closed(rec.pnl, i)
        if rec.pnl < 0 and self.s.risk.cooldown_bars_after_loss:
            self._event(self.state.risk_events, rec.closed_at, "COOLDOWN",
                        f"{rec.uid} lost {rec.pnl:.2f}: no new basket for {self.s.risk.cooldown_bars_after_loss} bars")

    # --------------------------------------------------------------- per bar
    def _records(self, f: pd.DataFrame) -> list[dict]:
        """Rows as plain dicts, built once per frame: pandas row access is far too slow per bar."""
        key = (id(f), len(f), f["timestamp"].iloc[0], f["timestamp"].iloc[-1]) if len(f) else (id(f), 0)
        if getattr(self, "_rec_key", None) != key:
            self._rec_key, self._rec = key, f.to_dict("records")
        return self._rec

    def process_bar(self, f: pd.DataFrame, i: int) -> None:
        st = self.state
        rows = self._records(f)
        row = rows[i]
        prev = rows[i - 1] if i > 0 else row
        ts = row["timestamp"].to_pydatetime()
        o, c = row["open"], row["close"]
        st.tracker.update(self._equity(st.balance, st.basket, o, row, o), ts)

        # 1. a pending entry fills at this bar's open
        if st.pending and st.pending["execute_at"] == i and st.basket is None:
            p = st.pending
            st.pending = None
            base = base_lot(self.s.sizing, self.inst, p["atr"], o)
            lots = lot_for_entry(1, self.s.sizing, self.inst, base)
            d = self.risk.check_open(lots, o, st.balance, i)
            p_sig = {"ts": p["signal_ts"], "signal": p["direction"], "regime": p["regime"],
                     "vol_regime": p["vol_regime"], "reason": p["reason"]}
            if not d.allowed:
                self._event(st.risk_events, ts, d.code, f"entry refused: {d.detail}")
                st.signals.append({**p_sig, "acted": False, "reason": p["reason"] + f" | refused: {d.code}"})
            else:
                st.basket_counter += 1
                loss, tgt, tdist = basket_limits(self.s, st.balance, p["atr"])
                bk = Basket(f"BASKET-{st.basket_counter:06d}", p["direction"], ts, i, st.balance, loss, tgt, tdist,
                            p["atr"], p["regime"], p["vol_regime"], base_lot=base)
                fill = self.ex.entry_fill(bk.direction, o, row)
                bk.positions.append(Position(1, bk.direction, lots, ts, fill, o, self.comm * lots))
                bk.last_entry_index = i
                bk.max_notional, bk.max_margin = notional(lots, o, self.inst), margin(lots, o, self.inst)
                st.basket = bk
                st.signals.append({**p_sig, "acted": True})

        # 2. manage the open basket through the bar
        if st.basket is not None:
            self._intrabar(i, row, prev, ts)

        # 3. time stop
        if st.basket is not None and self.s.stop.max_basket_bars and i - st.basket.open_index + 1 >= self.s.stop.max_basket_bars:
            br = _Branch(st.balance, st.basket)
            self._close(br, c, ts, i, "TIME_STOP", row, o)
            st.balance, st.basket = br.balance, None
            self._on_closed(br.closed, i)

        # 4. mark to market at the close; account drawdown check
        equity = self._equity(st.balance, st.basket, c, row, o)
        dd = st.tracker.update(equity, ts)
        if self.risk.check_account(equity) == "CLOSE_AND_HALT":
            reason = f"{ts:%Y-%m-%d %H:%M} UTC: account drawdown reached {self.s.risk.max_account_drawdown_percent}%"
            if st.basket is not None:
                br = _Branch(st.balance, st.basket)
                self._close(br, c, ts, i, "ACCOUNT_DRAWDOWN", row, o)
                st.balance, st.basket = br.balance, None
                self._on_closed(br.closed, i)
            self.risk.halt(reason)
            self._event(st.risk_events, ts, "HALTED", reason)
            equity = st.balance
        bk = st.basket
        st.curve.append(EquityPoint(ts, st.balance, equity, dd, bk.n if bk else 0, bk.total_lots if bk else 0.0,
                                    notional(bk.total_lots, c, self.inst) if bk else 0.0,
                                    margin(bk.total_lots, c, self.inst) if bk else 0.0))

        # 5. analyse this closed bar for the next basket
        execute_at = i + 1 + self.s.execution.entry_latency_bars
        if st.basket is None and st.pending is None and not self.risk.state.halted and i > st.last_close_index \
                and execute_at >= self.risk.state.cooldown_until and ready(row):
            dec = evaluate_entry(row, self.s.entry)
            if dec.direction != "NONE":
                st.pending = {"direction": dec.direction, "signal_index": i, "signal_ts": ts.isoformat(),
                              "execute_at": execute_at, "regime": row["regime"],
                              "vol_regime": row["vol_regime"], "atr": float(row["atr"]), "reason": "; ".join(dec.reasons)}
        st.bars_processed += 1

    def finish(self, f: pd.DataFrame) -> None:
        """Close whatever is still open at the last bar's close (END_OF_DATA)."""
        st = self.state
        if st.basket is not None:
            row = self._records(f)[-1]
            br = _Branch(st.balance, st.basket)
            self._close(br, row["close"], row["timestamp"].to_pydatetime(), len(f) - 1, "END_OF_DATA", row, row["open"])
            st.balance, st.basket = br.balance, None
            self._on_closed(br.closed, len(f) - 1)
            if st.curve:
                st.curve[-1].equity = st.balance
        st.pending = None
