"""
ADAPTIVE_GRID_BASKET strategy.

The cycle:
  closed bar -> analysis -> entry decision -> OPEN_BASKET intent
  ticks      -> grid re-entry levels / basket target / basket loss limit -> intents
  fills      -> basket updated; on close: record, cooldown, reset, back to analysis

This class only DECIDES. It never calls a broker or an executor and never
changes the account: it returns OrderIntents (plus event records for the
audit log) and is told about fills afterwards. The same object runs in a
backtest and in paper trading; only the source of bars and ticks differs.

Within one tick, price moves from `prev_mid` to the tick's mid. If that path
crosses several levels (a grid add, then the loss limit, say) the strategy
acts on the one price reaches first, and is called again for the rest of the
path after the fill. A level crossed continuously is triggered at the level
itself; a level the price was already beyond (a gap, a discrete live poll) is
triggered at the current price.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.config import GridMode, Settings
from app.execution.fill_engine import FillEngine
from app.execution.order_intent import Fill, IntentType, OrderIntent
from app.market.instruments import get_instrument
from app.market.market_engine import MarketState
from app.risk.risk_manager import RiskDecision
from app.strategy.analysis import Analysis, MarketAnalysisEngine
from app.strategy.basket_manager import Basket, BasketManager, CompletedBasket, Position, basket_limits
from app.strategy.entry_engine import EntryDecision, EntryEngine
from app.strategy.grid_engine import next_add_level
from app.strategy.position_sizing import lots_for_entry

PERSISTENT_ADD_BLOCKS = {"POSITION_LIMIT", "BASKET_DRAWDOWN", "EXPOSURE", "MARGIN"}


@dataclass
class StrategyView:
    """What the strategy may read about the account: values only, no handles to change anything."""
    equity: float
    data_ok: bool = True


@dataclass
class Event:
    type: str
    message: str
    data: dict = field(default_factory=dict)


class AdaptiveGridBasketStrategy:
    name = "ADAPTIVE_GRID_BASKET"

    def __init__(self, settings: Settings):
        self.s = settings
        self.inst = get_instrument(settings.market.symbol)
        self.analysis_engine = MarketAnalysisEngine(settings.entry, settings.analysis)
        self.entry = EntryEngine(settings.entry)
        self.fe = FillEngine(self.inst, settings.execution)
        self.baskets = BasketManager()
        self.bar = 0
        self.cooldown_until = 0
        self.pending_open = self.pending_add = self.pending_close = False
        self.add_retry_bar = -1
        self.last_state: MarketState | None = None
        self.last_analysis: Analysis | None = None
        self.last_decision: EntryDecision | None = None
        self.target_announced = False

    # ---------------------------------------------------------------- helpers
    @property
    def basket(self) -> Basket | None:
        return self.baskets.current

    @property
    def comm(self) -> float:
        return self.s.execution.commission_per_lot_side

    def _intent(self, type_: IntentType, direction: str, lots: float, reason: str, now: datetime,
                trigger: float | None = None, close_reason: str | None = None, **meta) -> OrderIntent:
        b = self.basket
        return OrderIntent(type_, self.inst.symbol, direction, lots, reason, now, b.uid if b else None, trigger,
                           close_reason, meta)

    def close_intent(self, close_reason: str, now: datetime, reason: str) -> OrderIntent | None:
        """A forced close (account drawdown, manual, end of data). Not subject to the risk gate."""
        b = self.basket
        if b is None or self.pending_close:
            return None
        self.pending_close = True
        return self._intent(IntentType.CLOSE_BASKET, b.direction, b.total_lots, reason, now, None, close_reason)

    # ---------------------------------------------------------------- closed bar
    def on_bar(self, state: MarketState, bar: int, view: StrategyView, now: datetime) -> tuple[list[OrderIntent], list[Event]]:
        self.bar, self.last_state = bar, state
        ev: list[Event] = []
        a = self.analysis_engine.analyze(state)
        self.last_analysis = a
        b = self.basket
        if b is not None:
            if self.s.grid.mode == GridMode.SIGNAL_CONFIRMED and b.adds_blocked is None:
                ok, why = self.entry.thesis_holds(state, b.direction)
                if not ok:
                    b.adds_blocked = f"thesis broken: {why}"
                    ev.append(Event("GRID_STOPPED", f"{b.uid}: original thesis no longer valid, no more adds ({why})"))
            return [], ev
        if self.pending_open:
            return [], ev
        if bar < self.cooldown_until:
            self.last_decision = EntryDecision("WAIT", [f"cooldown until bar {self.cooldown_until}"])
            return [], ev
        d = self.entry.decide(state, a)
        self.last_decision = d
        ev.append(Event("MARKET_ANALYZED", f"Market analyzed: {state.regime}, {d.action}",
                        {"analysis": a.to_dict(), "decision": d.action, "reasons": d.reasons}))
        if not d.is_entry or not view.data_ok:
            return [], ev
        lots = lots_for_entry(1, self.s.sizing, self.inst)
        ev.append(Event("SIGNAL", f"{d.action} signal generated (rule agreement {d.confidence:.2f})",
                        {"direction": d.action, "reasons": d.reasons}))
        self.pending_open = True
        it = OrderIntent(IntentType.OPEN_BASKET, self.inst.symbol, d.action, lots, "; ".join(d.reasons), now,
                         None, None, None, {"atr": state.atr, "regime": state.regime, "analysis": a.to_dict()})
        return [it], ev

    # ---------------------------------------------------------------- ticks
    def on_tick(self, state: MarketState, prev_mid: float, view: StrategyView) -> tuple[list[OrderIntent], list[Event]]:
        b = self.basket
        ev: list[Event] = []
        if b is None or self.pending_close or self.pending_add:
            return [], ev
        mid, sp, now = state.mid, state.spread, state.timestamp
        exit_now = self.fe.close_price(b.direction, mid, sp)
        pnl = b.pnl(exit_now, self.inst, mid, self.comm)
        b.mark(pnl, mid, self.inst)
        sg = b.sign

        cands: list[tuple[float, str, float | None]] = []   # (distance from prev along the path, kind, trigger)

        def reach(level: float, favourable: bool) -> None:
            beyond_now = sg * (mid - level) >= 0 if favourable else sg * (level - mid) >= 0
            if not beyond_now:
                return
            beyond_prev = sg * (prev_mid - level) >= 0 if favourable else sg * (level - prev_mid) >= 0
            if beyond_prev:
                cands.append((0.0, kind, None))           # already past it: act at the current price
            else:
                cands.append((abs(level - prev_mid), kind, level))

        kind = "TARGET"
        reach(self.fe.mid_for_close_price(b.direction, b.target_exit_price(self.inst, mid, self.comm), sp), True)
        kind = "LOSS_LIMIT"
        reach(self.fe.mid_for_close_price(b.direction, b.stop_exit_price(self.inst, mid, self.comm), sp), False)

        r = self.s.risk
        if b.adds_blocked is None and r.max_basket_drawdown_usd is not None and -pnl >= r.max_basket_drawdown_usd:
            b.adds_blocked = f"BASKET_DRAWDOWN: floating loss {-pnl:.2f} >= {r.max_basket_drawdown_usd:.2f}"
            ev.append(Event("GRID_STOPPED", f"{b.uid}: basket drawdown limit, no more adds"))
        lvl = None
        if (b.adds_blocked is None and view.data_ok and self.bar != self.add_retry_bar
                and self.bar - b.last_add_bar >= self.s.grid.min_bars_between_adds):
            atr = self.last_state.atr if self.last_state else None
            lvl = next_add_level(b.direction, b.last_ref_price, self.s.grid, self.s.sizing.mode, atr)
            if lvl is not None:
                kind = "ADD"
                reach(lvl.price, not lvl.adverse)
        if not cands:
            return [], ev
        dist, what, trig = min(cands, key=lambda c: (c[0], {"LOSS_LIMIT": 0, "TARGET": 1, "ADD": 2}[c[1]]))

        if what == "TARGET":
            ev.append(Event("TARGET_REACHED", f"{b.uid}: basket P&L reached target", {"pnl": pnl}))
            ev.append(Event("CLOSING", f"Closing {b.uid} ({b.n} positions)"))
            self.pending_close = True
            return [self._intent(IntentType.CLOSE_BASKET, b.direction, b.total_lots, "basket target reached",
                                 now, trig, "TARGET")], ev
        if what == "LOSS_LIMIT":
            if not self.s.risk.close_basket_on_limit:
                if b.adds_blocked is None:
                    b.adds_blocked = "LOSS_LIMIT reached (close_basket_on_limit is off)"
                    ev.append(Event("GRID_STOPPED", f"{b.uid}: loss limit reached, adds stopped (basket stays open)"))
                return [], ev
            ev.append(Event("LOSS_LIMIT", f"{b.uid}: basket loss limit {b.loss_limit_usd:.2f} USD reached", {"pnl": pnl}))
            ev.append(Event("CLOSING", f"Closing {b.uid} ({b.n} positions) at the loss limit"))
            self.pending_close = True
            return [self._intent(IntentType.CLOSE_BASKET, b.direction, b.total_lots, "basket loss limit",
                                 now, trig, "LOSS_LIMIT")], ev
        lots = lots_for_entry(b.n + 1, self.s.sizing, self.inst)
        why = "price reached the re-entry zone"
        if self.s.grid.mode == GridMode.SIGNAL_CONFIRMED:
            why += "; original thesis still valid at the last bar"
        ev.append(Event("REENTRY_ZONE", f"{b.uid}: price moved to re-entry zone {lvl.price:.5g} "
                        f"({'against' if lvl.adverse else 'with'} the basket, distance {lvl.distance:.5g})"))
        self.pending_add = True
        return [self._intent(IntentType.ADD_POSITION, b.direction, lots, why, now, trig,
                             level=lvl.price, distance=lvl.distance, entry_number=b.n + 1)], ev

    def active_levels(self, spread: float) -> list[float]:
        """Mid prices at which on_tick would act right now (target, loss limit, next grid add). Used by the
        backtester to place a tick exactly where price crosses a level when execution is delayed."""
        b = self.basket
        if b is None or self.pending_close or self.pending_add:
            return []
        mid = self.last_state.close if self.last_state else b.last_ref_price
        out = [self.fe.mid_for_close_price(b.direction, b.target_exit_price(self.inst, mid, self.comm), spread),
               self.fe.mid_for_close_price(b.direction, b.stop_exit_price(self.inst, mid, self.comm), spread)]
        if b.adds_blocked is None and self.bar != self.add_retry_bar:
            lvl = next_add_level(b.direction, b.last_ref_price, self.s.grid, self.s.sizing.mode,
                                 self.last_state.atr if self.last_state else None)
            if lvl is not None:
                out.append(lvl.price)
        return out

    # ---------------------------------------------------------------- outcomes
    def on_reject(self, intent: OrderIntent, decision: RiskDecision) -> list[Event]:
        ev = [Event("RISK_REJECTED", f"{intent.type.value} refused by risk manager: {decision.code} ({decision.detail})",
                    {"code": decision.code, "detail": decision.detail})]
        if intent.type == IntentType.OPEN_BASKET:
            self.pending_open = False
        elif intent.type == IntentType.ADD_POSITION:
            self.pending_add = False
            b = self.basket
            if decision.code in PERSISTENT_ADD_BLOCKS and b is not None:
                b.adds_blocked = f"{decision.code}: {decision.detail}"
                ev.append(Event("GRID_STOPPED", f"{b.uid}: no more adds ({decision.code})"))
            else:
                self.add_retry_bar = self.bar          # try again on a later bar
        return ev

    def on_cancel(self) -> None:
        self.pending_open = self.pending_add = self.pending_close = False

    def on_fill(self, f: Fill, equity: float) -> tuple[list[Event], CompletedBasket | None]:
        ev: list[Event] = []
        if f.type == IntentType.OPEN_BASKET:
            self.pending_open = False
            st = self.last_state
            atr = st.atr if st else 0.0
            loss, tgt, tdist = basket_limits(self.s, equity, atr)
            b = Basket(self.baskets.new_id(), f.direction, f.time, self.bar, equity, loss, tgt, tdist, atr,
                       st.regime if st else "", (self.last_analysis.to_dict() if self.last_analysis else {}))
            b.positions.append(Position(1, f.direction, f.lots, f.time, f.price, f.reference_mid, f.commission, f.reason))
            b.last_add_bar = self.bar
            self.baskets.open(b)
            self.target_announced = False
            ev.append(Event("POSITION_OPENED", f"{b.uid}: {f.direction} {f.lots:g} lots opened at {f.price:.5g}",
                            {"fill": f.to_dict()}))
            return ev, None
        b = self.basket
        if f.type == IntentType.ADD_POSITION:
            self.pending_add = False
            if b is None:
                return ev, None
            b.positions.append(Position(b.n + 1, f.direction, f.lots, f.time, f.price, f.reference_mid, f.commission,
                                        f.reason))
            b.last_add_bar = self.bar
            ev.append(Event("POSITION_ADDED", f"{b.uid}: additional {f.direction} {f.lots:g} lots at {f.price:.5g} "
                            f"(position {b.n}, average entry {b.avg_entry:.5g})", {"fill": f.to_dict()}))
            return ev, None
        # CLOSE_BASKET
        self.pending_close = self.pending_add = False
        if b is None:
            return ev, None
        exit_comm = self.comm * b.total_lots
        pnl = b.pnl(f.price, self.inst, f.reference_mid, self.comm)
        b.mark(pnl, f.reference_mid, self.inst)
        rec = self.baskets.close(f.price, pnl, f.time, self.bar, f.close_reason or "UNKNOWN")
        cool = self.s.risk.cooldown_bars_after_close + (self.s.risk.cooldown_bars_after_loss if pnl < 0 else 0)
        # self.bar = bars closed so far; the basket closed during bar self.bar + 1. That bar can never
        # signal the next basket, and `cool` more bars are skipped after it.
        self.cooldown_until = self.bar + 2 + cool
        ev.append(Event("BASKET_CLOSED", f"{rec.uid} completed: {rec.close_reason}, {rec.positions} positions, "
                        f"P&L {pnl:+.2f} USD", {"basket": rec.to_dict(), "exit_commission": exit_comm}))
        ev.append(Event("RESET", f"System reset: next basket needs a new signal after bar {self.cooldown_until - 1}"))
        return ev, rec
