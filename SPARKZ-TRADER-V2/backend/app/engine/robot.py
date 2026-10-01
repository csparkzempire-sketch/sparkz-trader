"""
The robot: wires market data, strategy, risk, execution and the paper account
together, and keeps the audit log and the status the dashboard shows.

  closed candle -> MarketEngine -> Strategy.on_bar  -> intents -+
  tick          -> MarketEngine -> Strategy.on_tick -> intents -+-> RiskManager -> SimulationExecutor
                                                                                       |
  account & basket <- Strategy.on_fill <---------------------- fills <-----------------+

The SAME Robot class runs a backtest (bars and synthetic intrabar ticks from
the backtester) and paper trading (bars and ticks from a live provider).

Status (ROBOT STATUS on the dashboard):
  ANALYZING, WAITING, ENTRY, ADDING_POSITION, MANAGING_BASKET, TARGET_REACHED,
  CLOSING, COOLDOWN, STOPPED

Safety:
- EMERGENCY STOP: no new entries, no grid adds, no strategy processing, pending
  orders cancelled, simulation state preserved (an open basket stays open and is
  still marked to market). Only a person can resume.
- Account drawdown limit: the open basket is closed and the robot STOPS until
  a person resumes it.
- Stale data (paper): no new positions until fresh data arrives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.config import TIMEFRAMES, Settings
from app.engine.event_log import EventLog
from app.execution.order_intent import Fill, IntentType, OrderIntent
from app.execution.simulator import SimulationExecutor
from app.market.instruments import get_instrument
from app.market.market_engine import MarketEngine, MarketState
from app.market.providers.base import Tick
from app.paper.account import PaperAccount
from app.paper.positions import open_positions
from app.paper.trades import TradeLedger
from app.risk.risk_manager import RiskContext, RiskManager
from app.strategy.basket_manager import CompletedBasket
from app.strategy.position_sizing import describe as describe_sizing
from app.strategy.strategy import AdaptiveGridBasketStrategy, Event, StrategyView

STATUSES = ["ANALYZING", "WAITING", "ENTRY", "ADDING_POSITION", "MANAGING_BASKET", "TARGET_REACHED", "CLOSING",
            "COOLDOWN", "STOPPED"]


@dataclass
class EquityPoint:
    time: datetime
    equity: float
    balance: float
    floating: float
    positions: int
    lots: float = 0.0
    notional: float = 0.0
    margin: float = 0.0
    price: float = 0.0


class Robot:
    def __init__(self, settings: Settings, mode: str = "PAPER", precomputed: pd.DataFrame | None = None,
                 log: EventLog | None = None, log_analysis: bool = True, keep_fills: int | None = None):
        self.s, self.mode = settings, mode
        self.inst = get_instrument(settings.market.symbol)
        self.bar_seconds = TIMEFRAMES[settings.market.timeframe]
        self.market = MarketEngine(settings, precomputed)
        self.strategy = AdaptiveGridBasketStrategy(settings)
        self.account = PaperAccount(settings.risk.initial_capital)
        self.risk = RiskManager(settings, self.inst, self.account.dd)
        self.executor = SimulationExecutor(self.inst, settings.execution)
        self.ledger = TradeLedger(keep=keep_fills)
        self.log = log or EventLog()
        self.log_analysis = log_analysis
        self.bar = 0
        self.status = "WAITING"
        self.emergency: str | None = None
        self.halted: str | None = None
        self.data_ok, self.data_reason = True, ""
        self.last_mid: float | None = None
        self.last_tick: Tick | None = None
        self.equity_curve: list[EquityPoint] = []
        self.completed: list[CompletedBasket] = []
        self.peak_positions = 0

    # ------------------------------------------------------------------ status
    @property
    def stopped(self) -> str | None:
        return self.emergency or self.halted

    def _set_status(self, status: str, when: datetime, why: str = "") -> None:
        if status != self.status:
            prev, self.status = self.status, status
            routine = status == "ANALYZING" or (prev == "ANALYZING" and status == "WAITING")
            if (self.mode == "PAPER" and not routine) or status == "STOPPED":
                self.log.add(when, "STATUS", f"Robot status: {status}" + (f" ({why})" if why else ""))

    def _derive_status(self, when: datetime) -> None:
        st = self.strategy
        if self.stopped:
            self._set_status("STOPPED", when, self.stopped)
        elif st.pending_close:
            self._set_status("CLOSING", when)
        elif st.pending_add:
            self._set_status("ADDING_POSITION", when)
        elif st.pending_open:
            self._set_status("ENTRY", when)
        elif st.basket is not None:
            self._set_status("MANAGING_BASKET", when)
        elif self.bar < st.cooldown_until:
            self._set_status("COOLDOWN", when)
        else:
            self._set_status("WAITING", when)

    def _emit(self, when: datetime, events: list[Event]) -> None:
        for e in events:
            if e.type == "MARKET_ANALYZED" and not self.log_analysis:
                continue
            self.log.add(when, e.type, e.message, e.data)

    # ------------------------------------------------------------------ marking
    def _mark(self, when: datetime) -> None:
        b = self.strategy.basket
        lt = self.market.live or self.market.last_bar
        if b is None or lt is None:
            self.account.mark(0.0, 0.0, 0, 0.0, when)
            return
        fe = self.strategy.fe
        exit_px = fe.close_price(b.direction, lt.mid, lt.spread)
        pnl = b.pnl(exit_px, self.inst, lt.mid, self.s.execution.commission_per_lot_side)
        b.mark(pnl, lt.mid, self.inst)
        self.account.mark(pnl, self.inst.margin_usd(b.total_lots, lt.mid), b.n, b.total_lots, when)

    def _risk_ctx(self, price: float) -> RiskContext:
        b = self.strategy.basket
        return RiskContext(self.account.equity, price, b.n if b else 0, b.total_lots if b else 0.0,
                           b.last_pnl if b else 0.0, self.bar, self.strategy.cooldown_until, self.stopped, self.data_ok)

    # ------------------------------------------------------------------ order flow
    def _submit(self, intents: list[OrderIntent], when: datetime, price: float) -> int:
        n = 0
        for it in intents:
            d = self.risk.check(it, self._risk_ctx(price))
            if not d.allowed:
                self._emit(when, self.strategy.on_reject(it, d))
                continue
            if it.type == IntentType.ADD_POSITION:
                self.log.add(when, "GRID_CHECK", f"Grid conditions checked: additional {it.direction} permitted "
                             f"({it.reason})", {"intent": it.to_dict()})
            self.log.add(when, "ORDER_INTENT", f"{it.type.value} {it.direction} {it.lots:g} lots sent to the "
                         "simulated executor", {"intent": it.to_dict()})
            self.executor.submit(it, when, it.trigger_price if it.trigger_price is not None else price)
            n += 1
        return n

    def _apply(self, fills: list[Fill]) -> None:
        for f in fills:
            self.ledger.record(f)
            events, rec = self.strategy.on_fill(f, self.account.equity)
            self._emit(f.time, events)
            if rec is not None:
                self.account.realize(rec.pnl, f.time)
                self.completed.append(rec)
                self.log.add(f.time, "ACCOUNT_UPDATED", f"Balance {self.account.balance:.2f}, "
                             f"realized P&L {self.account.realized_pnl:+.2f}")
            b = self.strategy.basket
            if b is not None:
                self.peak_positions = max(self.peak_positions, b.n)
            self._mark(f.time)

    # ------------------------------------------------------------------ inputs
    def warm_up(self, candles: pd.DataFrame) -> None:
        """Paper start: load closed history (counts as bars already seen; no trading on them)."""
        st = self.market.load_history(candles)
        if st is not None:
            self.log.add(st.timestamp, "MARKET_DATA", f"Loaded {len(self.market.candles)} closed candles; "
                         f"last {st.timestamp:%Y-%m-%d %H:%M} UTC, regime {st.regime}")

    def process_bar_close(self, candle, index: int | None = None) -> MarketState | None:
        st = self.market.on_candle_close(candle, index)
        if st is None:
            return None
        self.bar += 1
        when = st.timestamp + timedelta(seconds=self.bar_seconds)
        self._mark(when)
        a = self.account
        self.equity_curve.append(EquityPoint(when, a.equity, a.balance, a.floating_pnl, a.open_positions, a.open_lots,
                                             self.inst.notional_usd(a.open_lots, st.close), a.used_margin, st.close))
        if self.stopped:
            self._derive_status(when)
            return st
        if self.strategy.basket is None and not self.strategy.pending_open:
            self._set_status("ANALYZING", when)
        intents, events = self.strategy.on_bar(st, self.bar, StrategyView(a.equity, self.data_ok), when)
        self._emit(when, events)
        self._submit(intents, when, st.close)
        self._derive_status(when)
        return st

    def process_tick(self, tick: Tick) -> None:
        st = self.market.on_tick(tick)
        self.last_tick = tick
        if st is None:
            return
        prev = self.last_mid if self.last_mid is not None else tick.mid
        self._apply(self.executor.process(tick))          # orders that became due
        self._mark(tick.time)
        if not self.halted and self.risk.check_account(self.account.equity) == "CLOSE_AND_HALT":
            self._halt(tick.time)
            self._apply(self.executor.process(tick))
        if self.stopped:
            self.last_mid = tick.mid
            self._derive_status(tick.time)
            return
        view = StrategyView(self.account.equity, self.data_ok)
        for _ in range(4 * self.s.risk.max_positions + 4):
            intents, events = self.strategy.on_tick(st, prev, view)
            self._emit(tick.time, events)
            if not intents:
                break
            if not self._submit(intents, tick.time, tick.mid):
                # refused (an add, never a close): the strategy has recorded the refusal, so asking
                # again evaluates the rest of the path, e.g. a loss limit beyond the refused add
                continue
            fills = self.executor.process(tick)
            if not fills:
                break           # delayed execution: fills arrive on a later tick
            self._apply(fills)
            prev = fills[-1].reference_mid
            view = StrategyView(self.account.equity, self.data_ok)
        self.last_mid = tick.mid
        self._derive_status(tick.time)

    # ------------------------------------------------------------------ controls
    def _halt(self, when: datetime) -> None:
        dd = self.account.dd.current_pct(self.account.equity)
        self.halted = f"account drawdown {dd:.1f}% reached the {self.s.risk.max_account_drawdown_percent}% limit"
        self.log.add(when, "HALT", f"ACCOUNT DRAWDOWN LIMIT: {self.halted}. Closing the basket and stopping the robot.")
        it = self.strategy.close_intent("ACCOUNT_DRAWDOWN", when, "account drawdown limit")
        if it is not None:
            self.executor.submit(it, when)
        self._set_status("STOPPED", when, self.halted)

    def emergency_stop(self, reason: str = "STOP ROBOT pressed", when: datetime | None = None) -> None:
        when = when or (self.last_tick.time if self.last_tick else datetime.now(timezone.utc))
        if self.emergency:
            return
        self.emergency = reason
        cancelled = self.executor.cancel_all()
        self.strategy.on_cancel()
        self.log.add(when, "EMERGENCY_STOP", f"EMERGENCY STOP: {reason}. New entries, grid expansion and strategy "
                     f"processing stopped; {len(cancelled)} pending order(s) cancelled; positions preserved.",
                     {"cancelled": [i.to_dict() for i in cancelled]})
        self._set_status("STOPPED", when, reason)

    def resume(self, when: datetime | None = None) -> None:
        """A person decides to restart after an emergency stop or a drawdown halt."""
        when = when or (self.last_tick.time if self.last_tick else datetime.now(timezone.utc))
        was = self.stopped
        self.emergency = None
        if self.halted:
            self.halted = None
            self.account.dd.peak = self.account.equity     # the drawdown limit restarts from here
        self.log.add(when, "RESUMED", f"Robot resumed by the user (was: {was})")
        self._derive_status(when)

    def close_basket_manually(self, when: datetime | None = None) -> bool:
        when = when or (self.last_tick.time if self.last_tick else datetime.now(timezone.utc))
        it = self.strategy.close_intent("MANUAL", when, "closed by the user")
        if it is None:
            return False
        self.log.add(when, "MANUAL_CLOSE", f"User asked to close {it.basket_id}")
        self.executor.submit(it, when)
        if self.last_tick is not None:
            self._apply(self.executor.process(self.last_tick))
        self._derive_status(when)
        return True

    def set_data_status(self, ok: bool, reason: str, when: datetime) -> None:
        if ok != self.data_ok:
            self.data_ok, self.data_reason = ok, reason
            self.log.add(when, "DATA_OK" if ok else "DATA_STALE",
                         ("Market data fresh again: " if ok else "MARKET DATA STALE, new entries blocked: ") + reason)

    # ------------------------------------------------------------------ dashboard
    def snapshot(self) -> dict:
        lt = self.market.live or self.market.last_bar
        b = self.strategy.basket
        fe = self.strategy.fe
        basket = None
        if b is not None and lt is not None:
            basket = b.status(fe.close_price(b.direction, lt.mid, lt.spread), lt.mid, lt.atr or 0.0, self.inst,
                              fe.half_cost(lt.spread), self.s.execution.commission_per_lot_side)
        a = self.strategy.last_analysis
        d = self.strategy.last_decision
        return {
            "mode": self.mode, "environment": self.s.environment.value, "live_trading_enabled": False,
            "strategy": self.strategy.name, "video_style_mode": self.s.video_style_mode, "preset": self.s.name,
            "status": self.status, "emergency_stop": self.emergency, "halted": self.halted,
            "data_ok": self.data_ok, "data_reason": self.data_reason, "bar": self.bar,
            "market": lt.to_dict() if lt else None,
            "analysis": a.to_dict() if a else None,
            "signal": {"action": d.action, "reasons": d.reasons} if d else None,
            "basket": basket,
            "positions": open_positions(b, fe.close_price(b.direction, lt.mid, lt.spread), lt.mid, self.inst)
            if b and lt else [],
            "account": self.account.snapshot(),
            "pending_orders": [i.to_dict() for i in self.executor.pending()],
            "completed_baskets": len(self.completed),
            "sizing": describe_sizing(self.s.sizing, self.s.risk.max_positions, self.inst),
            "high_risk_settings": self.s.high_risk,
        }
