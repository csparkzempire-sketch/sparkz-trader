"""
Broker adapter interface: the seam where a live broker could be added in a
later version. Version 1 ships only the simulated broker.

`get_broker` fails closed: asking for anything live raises, whatever the
configuration says, and there is no code path that sends a real order.
Credentials would be read from server-side environment variables by a
future adapter, never from frontend code.
"""

from __future__ import annotations

from typing import Protocol

from app.config import Settings


class BrokerAdapter(Protocol):
    name: str

    def place_market_order(self, symbol: str, direction: str, lots: float) -> dict: ...
    def close_all(self, symbol: str) -> dict: ...


class LiveTradingDisabled(RuntimeError):
    pass


class SimulatedBroker:
    """Orders are filled by the engine's execution model; nothing leaves this process."""
    name = "simulated"

    def place_market_order(self, symbol: str, direction: str, lots: float) -> dict:
        return {"simulated": True, "symbol": symbol, "direction": direction, "lots": lots}

    def close_all(self, symbol: str) -> dict:
        return {"simulated": True, "symbol": symbol}


def get_broker(settings: Settings, live: bool = False) -> BrokerAdapter:
    if live or settings.live_trading_enabled:
        raise LiveTradingDisabled("Live trading is not implemented in version 1 (fail closed).")
    return SimulatedBroker()
