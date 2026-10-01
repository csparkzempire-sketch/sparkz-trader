"""
Yahoo Finance provider: free, no credentials, DELAYED.

Good enough to watch the robot behave on real recent prices, not a substitute
for a broker feed:
- XAUUSD is COMEX gold futures (GC=F), a proxy for spot gold;
- quotes are typically delayed (around 10-20 minutes for futures);
- there is no bid/ask, so the tick uses the last 1-minute close with the
  instrument's typical spread around it.
The provider reports itself as delayed so the dashboard can say so.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.market.instruments import get_instrument
from app.market.providers.base import MarketDataProvider, MarketStatus, ProviderError, ProviderInfo, Tick
from app.market.sessions import market_open

INTERVAL = {"1m": ("1m", "5d"), "5m": ("5m", "30d"), "15m": ("15m", "55d"), "30m": ("30m", "55d"),
            "1h": ("60m", "700d"), "4h": ("60m", "700d")}


def _download(symbol: str, interval: str, period: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover
        raise ProviderError("yfinance is not installed") from exc
    try:
        df = yf.Ticker(symbol).history(interval=interval, period=period, auto_adjust=False)
    except Exception as exc:  # network / rate limit
        raise ProviderError(f"Yahoo download failed: {exc}") from exc
    if df is None or df.empty:
        raise ProviderError(f"Yahoo returned no data for {symbol} {interval}")
    df = df.reset_index().rename(columns=str.lower)
    tcol = "datetime" if "datetime" in df.columns else "date"
    out = pd.DataFrame({"timestamp": pd.to_datetime(df[tcol], utc=True), "open": df["open"], "high": df["high"],
                        "low": df["low"], "close": df["close"], "volume": df.get("volume", 0.0)})
    return out.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = df.set_index("timestamp").resample(rule, label="left", closed="left")
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return out.reset_index()


class YahooProvider(MarketDataProvider):
    def __init__(self, symbol: str = "XAUUSD", timeframe: str = "15m", downloader=_download):
        self.instrument = get_instrument(symbol)
        self.timeframe = timeframe
        self._dl = downloader

    def provider_symbol(self) -> str:
        return self.instrument.yahoo

    def info(self) -> ProviderInfo:
        notes = ["delayed public data; no bid/ask (spread is the instrument default)"]
        if self.instrument.note:
            notes.append(self.instrument.note)
        return ProviderInfo("Yahoo Finance", "YAHOO", live=True, delayed=True, notes=notes)

    def get_latest_tick(self) -> Tick:
        df = self._dl(self.instrument.yahoo, "1m", "1d")
        last = df.iloc[-1]
        half = self.instrument.spread / 2
        c = float(last["close"])
        return Tick(self.instrument.symbol, pd.Timestamp(last["timestamp"]).to_pydatetime(), c - half, c + half)

    def get_candles(self, timeframe: str, count: int) -> pd.DataFrame:
        interval, period = INTERVAL[timeframe]
        df = self._dl(self.instrument.yahoo, interval, period)
        if timeframe == "4h":
            df = resample(df, "4h")
        step = pd.Timedelta(timeframe.replace("m", "min"))
        closed = df[df["timestamp"] + step <= pd.Timestamp.now(tz="UTC")]   # drop the forming candle
        return closed.tail(count).reset_index(drop=True)

    def get_market_status(self) -> MarketStatus:
        now = datetime.now(timezone.utc)
        ok, why = market_open(self.instrument, now)
        return MarketStatus(ok, why, now)
