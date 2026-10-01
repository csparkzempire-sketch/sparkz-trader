"""
Execution adapters.

ExecutionAdapter is the only thing that turns an approved OrderIntent into a
Fill. V2 ships exactly one working adapter, SimulationExecutor. There is no
live adapter: LiveExecutionAdapter exists only to fail loudly if anything ever
tries to build one.

SimulationExecutor:
- queues each intent with a due time = submission time + execution.delay_ms;
- on each tick, fills the intents that are due, at the FillEngine price;
- level-triggered intents filled without delay on a continuous price path use
  their trigger level; otherwise (gap, discrete live poll, any delay) the
  current tick's mid is used, so latency costs what the market moved.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from app.config import ExecutionCfg
from app.execution.fill_engine import FillEngine
from app.execution.order_intent import Fill, IntentType, OrderIntent
from app.market.instruments import Instrument
from app.market.providers.base import Tick


class ExecutionAdapter(ABC):
    @abstractmethod
    def submit(self, intent: OrderIntent, now: datetime) -> None: ...

    @abstractmethod
    def process(self, tick: Tick) -> list[Fill]: ...

    @abstractmethod
    def pending(self) -> list[OrderIntent]: ...

    @abstractmethod
    def cancel_all(self) -> list[OrderIntent]: ...


class LiveExecutionAdapter(ExecutionAdapter):  # pragma: no cover - must never work in V2
    def __new__(cls, *a, **kw):
        raise PermissionError("Live execution is not available in SPARKZ TRADER V2. All orders are simulated.")


class SimulationExecutor(ExecutionAdapter):
    simulated = True

    def __init__(self, inst: Instrument, cfg: ExecutionCfg):
        self.fills = FillEngine(inst, cfg)
        self.delay = timedelta(milliseconds=cfg.delay_ms)
        self._queue: list[tuple[datetime, OrderIntent]] = []
        self.ref_mid: dict[int, float] = {}       # intent id -> mid when the order was created

    def submit(self, intent: OrderIntent, now: datetime, ref_mid: float | None = None) -> None:
        self._queue.append((now + self.delay, intent))
        if ref_mid is not None:
            self.ref_mid[intent.id] = ref_mid

    def pending(self) -> list[OrderIntent]:
        return [i for _, i in self._queue]

    def next_due(self) -> datetime | None:
        return min((t for t, _ in self._queue), default=None)

    def due_ref(self) -> float | None:
        """Creation-time mid of the next order to fall due (backtest latency model)."""
        if not self._queue:
            return None
        t, it = min(self._queue, key=lambda x: x[0])
        return self.ref_mid.get(it.id, it.trigger_price)

    def cancel_all(self) -> list[OrderIntent]:
        out = self.pending()
        self._queue.clear()
        self.ref_mid.clear()
        return out

    def process(self, tick: Tick) -> list[Fill]:
        due = [(t, i) for t, i in self._queue if t <= tick.time]
        self._queue = [(t, i) for t, i in self._queue if t > tick.time]
        out = []
        for _, it in due:
            self.ref_mid.pop(it.id, None)
            use_trigger = it.trigger_price is not None and not tick.gap and self.delay.total_seconds() == 0
            mid = it.trigger_price if use_trigger else tick.mid
            if it.type == IntentType.CLOSE_BASKET:
                side = "SELL" if it.direction == "BUY" else "BUY"
            else:
                side = it.direction
            price = self.fills.price(side, mid, tick.spread)
            out.append(Fill(it.id, it.type, it.symbol, it.direction, it.lots, price, mid,
                            self.fills.spread(tick.spread), self.fills.slippage(), self.fills.commission(it.lots),
                            tick.time, it.basket_id, it.reason, it.close_reason))
        return out
