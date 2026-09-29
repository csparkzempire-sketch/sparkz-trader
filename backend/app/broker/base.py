"""
Broker adapter interface.

Every broker (the local mock today, any real one later) implements
BrokerAdapter. The safety rule lives HERE, in the base class, not in each
adapter: `place_order` refuses to submit anything through an adapter that
reports `is_live = True` unless `settings.live_trading_enabled` is true.
Adapters implement `_submit_order`, never `place_order`, so a new adapter
cannot forget the check.

No real broker adapter exists in this codebase. See app.broker.factory.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from app.config import Settings, settings
from app.utils.time import utc_now

SIDES = ("BUY", "SELL")


class BrokerError(RuntimeError):
    """Base class for broker failures."""


class LiveTradingDisabledError(BrokerError):
    """Raised when a live adapter is used while LIVE_TRADING_ENABLED is false."""


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str  # "BUY" or "SELL"
    size: float
    client_order_id: str = field(default_factory=lambda: f"ord_{uuid.uuid4().hex[:12]}")

    def __post_init__(self):
        if self.side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}, got {self.side!r}")
        if not self.size > 0:
            raise ValueError(f"size must be > 0, got {self.size!r}")


@dataclass(frozen=True)
class OrderResult:
    client_order_id: str
    symbol: str
    side: str
    size: float
    status: str  # "FILLED" or "REJECTED"
    fill_price: float | None
    timestamp: datetime
    reason: str | None = None


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    net_size: float  # positive = long, negative = short
    average_price: float


@dataclass(frozen=True)
class AccountSnapshot:
    balance: float  # realized cash
    equity: float  # balance + unrealized PnL at the latest known prices
    open_positions: int


class BrokerAdapter(ABC):
    name: str = "base"
    is_live: bool = False  # True for anything that can move real money

    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or settings

    def place_order(self, order: OrderRequest) -> OrderResult:
        if self.is_live and not self.cfg.live_trading_enabled:
            raise LiveTradingDisabledError(
                f"Refusing to send order {order.client_order_id} through live broker '{self.name}': "
                "LIVE_TRADING_ENABLED is false."
            )
        return self._submit_order(order)

    @abstractmethod
    def _submit_order(self, order: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def get_positions(self) -> list[BrokerPosition]: ...

    @abstractmethod
    def get_account(self) -> AccountSnapshot: ...

    def close_position(self, symbol: str) -> OrderResult | None:
        """Flatten `symbol` with an opposite market order. None if already flat."""
        for pos in self.get_positions():
            if pos.symbol == symbol and pos.net_size != 0:
                side = "SELL" if pos.net_size > 0 else "BUY"
                return self.place_order(OrderRequest(symbol=symbol, side=side, size=abs(pos.net_size)))
        return None

    @staticmethod
    def _now() -> datetime:
        return utc_now()
