"""
Broker selection. This is the single entry point for getting a broker.

- "mock" always works: it's local and moves no money.
- Any other name is treated as a real broker. It fails closed: with
  LIVE_TRADING_ENABLED=false it raises LiveTradingDisabledError, and even
  with it true it raises BrokerNotAvailableError, because no real adapter
  exists in this build. A real adapter should only be registered here after
  extensive paper-trading validation and explicit, separate user consent.
"""

from __future__ import annotations

from app.broker.base import BrokerAdapter, BrokerError, LiveTradingDisabledError
from app.broker.mock import MockBroker
from app.config import Settings, settings

AVAILABLE_BROKERS = ("mock",)


class BrokerNotAvailableError(BrokerError):
    """Raised when a broker name has no adapter in this build."""


def get_broker(name: str = "mock", cfg: Settings | None = None) -> BrokerAdapter:
    cfg = cfg or settings
    key = name.strip().lower()
    if key == "mock":
        return MockBroker(cfg)
    if not cfg.live_trading_enabled:
        raise LiveTradingDisabledError(
            f"Broker '{name}' would be a live broker, and LIVE_TRADING_ENABLED is false."
        )
    raise BrokerNotAvailableError(
        f"No adapter for broker '{name}' exists in this build. Available: {', '.join(AVAILABLE_BROKERS)}."
    )
