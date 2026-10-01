"""
Market-data provider interface.

Every source of prices (synthetic, historical, delayed public data, a broker's
API) implements MarketDataProvider. The rest of the system only sees this
interface, so no part of it is tied to one broker.

Providers supply MARKET DATA ONLY. There is deliberately no order method
anywhere in this package: orders go Strategy -> OrderIntent -> ExecutionAdapter,
and the only adapter in V2 is the simulator.

Conventions:
- times are timezone-aware UTC;
- candles are labelled by their OPEN time; `get_candles` returns CLOSED
  candles only, oldest first (a still-forming candle is never included);
- a Tick carries bid and ask; mid and spread are derived.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Callable

import pandas as pd

from app.market.instruments import Instrument


@dataclass(frozen=True)
class Tick:
    symbol: str
    time: datetime
    bid: float
    ask: float
    gap: bool = False          # set by the backtester on a bar open that gapped from the previous close

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    provider_symbol: str
    contract_size: float
    point: float
    min_lot: float
    lot_step: float
    margin_rate: float
    quote_ccy: str


@dataclass
class MarketStatus:
    open: bool
    reason: str = ""
    checked_at: datetime | None = None


@dataclass
class ProviderInfo:
    name: str
    kind: str
    live: bool                 # True if prices are current (not replayed or synthetic)
    delayed: bool = False      # True if "current" prices are known to lag (e.g. Yahoo)
    notes: list[str] = field(default_factory=list)


class ProviderError(RuntimeError):
    """A provider could not deliver data (network, credentials, unsupported request)."""


class MarketDataProvider(ABC):
    instrument: Instrument

    @abstractmethod
    def info(self) -> ProviderInfo: ...

    @abstractmethod
    def get_latest_tick(self) -> Tick: ...

    @abstractmethod
    def get_candles(self, timeframe: str, count: int) -> pd.DataFrame:
        """The last `count` CLOSED candles: columns timestamp (UTC, open time), open, high, low, close, volume."""

    def get_symbol_info(self) -> SymbolInfo:
        i = self.instrument
        return SymbolInfo(i.symbol, self.provider_symbol(), i.contract_size, i.point, i.min_lot, i.lot_step,
                          i.margin_rate, i.quote_ccy)

    def provider_symbol(self) -> str:
        return self.instrument.symbol

    def get_spread(self) -> float:
        return self.get_latest_tick().spread

    @abstractmethod
    def get_market_status(self) -> MarketStatus: ...

    async def subscribe_to_market_data(self, interval_seconds: float = 5.0,
                                       stop: Callable[[], bool] | None = None) -> AsyncIterator[Tick]:
        """Polling subscription: yields a tick every `interval_seconds` until `stop()` is true.
        Providers with a streaming API can override this."""
        import asyncio

        while not (stop and stop()):
            try:
                yield self.get_latest_tick()
            except ProviderError:
                pass  # the paper loop's stale-data guard handles gaps
            await asyncio.sleep(interval_seconds)
