"""Provider factory: builds the configured MarketDataProvider."""

from __future__ import annotations

from app.config import ProviderKind, Settings
from app.market.providers.base import MarketDataProvider


def make_provider(settings: Settings, **kw) -> MarketDataProvider:
    m = settings.market
    if m.provider == ProviderKind.MOCK:
        from app.market.providers.mock_provider import MockProvider

        return MockProvider(m.symbol, m.timeframe, history_bars=m.history_bars, **kw)
    if m.provider == ProviderKind.YAHOO:
        from app.market.providers.yahoo_provider import YahooProvider

        return YahooProvider(m.symbol, m.timeframe, **kw)
    if m.provider == ProviderKind.BROKER:
        from app.market.providers.broker_provider import BrokerProvider

        return BrokerProvider(m.symbol, m.timeframe, **kw)
    if m.provider == ProviderKind.HISTORICAL:
        from app.market.history import load_history
        from app.market.providers.historical_provider import HistoricalDataProvider

        return HistoricalDataProvider(load_history(m.symbol, m.timeframe), m.symbol, m.timeframe)
    raise ValueError(m.provider)
