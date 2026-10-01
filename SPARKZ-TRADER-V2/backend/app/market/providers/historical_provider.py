"""
Historical provider: stored candles, replayed one bar at a time.

The backtester advances `cursor`; every query only sees candles up to and
including the cursor bar, so nothing in the strategy can read a future candle
through the provider.
"""

from __future__ import annotations

import pandas as pd

from app.market.instruments import get_instrument
from app.market.providers.base import MarketDataProvider, MarketStatus, ProviderInfo, Tick


class HistoricalDataProvider(MarketDataProvider):
    def __init__(self, candles: pd.DataFrame, symbol: str = "XAUUSD", timeframe: str = "15m", label: str = "stored"):
        if not candles["timestamp"].is_monotonic_increasing:
            raise ValueError("candles must be sorted by time")
        self.instrument = get_instrument(symbol)
        self.timeframe, self.label = timeframe, label
        self.candles = candles.reset_index(drop=True)
        self.cursor = 0

    def __len__(self) -> int:
        return len(self.candles)

    def info(self) -> ProviderInfo:
        return ProviderInfo(f"Historical ({self.label})", "HISTORICAL", live=False)

    def bar(self, i: int) -> pd.Series:
        if i > self.cursor:
            raise IndexError("look-ahead: bar beyond the replay cursor")
        return self.candles.iloc[i]

    def get_latest_tick(self) -> Tick:
        row = self.candles.iloc[self.cursor]
        half = self.instrument.spread / 2
        c = float(row["close"])
        return Tick(self.instrument.symbol, pd.Timestamp(row["timestamp"]).to_pydatetime(), c - half, c + half)

    def get_candles(self, timeframe: str, count: int) -> pd.DataFrame:
        if timeframe != self.timeframe:
            raise ValueError(f"historical data is {self.timeframe}")
        lo = max(0, self.cursor + 1 - count)
        return self.candles.iloc[lo:self.cursor + 1].reset_index(drop=True)

    def get_market_status(self) -> MarketStatus:
        return MarketStatus(True, "replay")
