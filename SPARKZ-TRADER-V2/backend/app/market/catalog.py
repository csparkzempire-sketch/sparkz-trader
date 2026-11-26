"""
Markets for the Chart page beyond the MT5 bridge: forex, commodities, index futures, stocks and crypto from Yahoo
Finance (free, no account; delayed about 10-20 minutes for futures and stocks, near real time for crypto and forex).
Chart data only: the robot never trades these.

Each timeframe is fetched once a minute at most (daily once every 5 minutes) and shared by every viewer.
"""

from __future__ import annotations

import re
import threading
import time

import pandas as pd

CATALOG: dict[str, list[tuple[str, str]]] = {
    "Forex": [("EURUSD=X", "EUR/USD"), ("GBPUSD=X", "GBP/USD"), ("JPY=X", "USD/JPY"), ("AUDUSD=X", "AUD/USD"),
              ("CAD=X", "USD/CAD"), ("CHF=X", "USD/CHF"), ("NZDUSD=X", "NZD/USD"), ("EURJPY=X", "EUR/JPY"),
              ("GBPJPY=X", "GBP/JPY"), ("EURGBP=X", "EUR/GBP")],
    "Commodities (futures)": [("GC=F", "Gold"), ("SI=F", "Silver"), ("CL=F", "Crude oil WTI"), ("BZ=F", "Brent oil"),
                              ("NG=F", "Natural gas"), ("HG=F", "Copper"), ("PL=F", "Platinum")],
    "Index futures": [("ES=F", "S&P 500"), ("NQ=F", "Nasdaq 100"), ("YM=F", "Dow Jones"), ("RTY=F", "Russell 2000"),
                      ("^GDAXI", "DAX (index)"), ("^FTSE", "FTSE 100 (index)"), ("^N225", "Nikkei 225 (index)")],
    "Stocks": [("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "Nvidia"), ("TSLA", "Tesla"), ("AMZN", "Amazon"),
               ("GOOGL", "Alphabet"), ("META", "Meta"), ("NFLX", "Netflix"), ("AMD", "AMD"), ("JPM", "JPMorgan")],
    "Crypto": [("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"), ("SOL-USD", "Solana"), ("XRP-USD", "XRP"),
               ("BNB-USD", "BNB"), ("DOGE-USD", "Dogecoin"), ("ADA-USD", "Cardano")],
}
NAMES = {s: n for items in CATALOG.values() for s, n in items}
VALID = re.compile(r"^[A-Za-z0-9^][A-Za-z0-9.=\-^]{0,19}$")
# timeframe -> (Yahoo interval, period, built from)
YAHOO_TF = {"5m": ("5m", "55d"), "15m": ("15m", "55d"), "1h": ("60m", "700d"), "1d": ("1d", "10y")}


# typical retail spread as a share of price, by kind (Yahoo has no bid/ask): used for the setups' cost
SPREAD_SHARE = {"Forex": 0.0001, "Commodities (futures)": 0.0003, "Index futures": 0.0001, "Stocks": 0.0003,
                "Crypto": 0.0005}


def category_of(symbol: str) -> str:
    for cat, items in CATALOG.items():
        if any(s == symbol for s, _ in items):
            return cat
    if symbol.endswith("=X"):
        return "Forex"
    if symbol.endswith("=F"):
        return "Commodities (futures)"
    if symbol.startswith("^"):
        return "Index futures"
    if re.search(r"-(USD|USDT|EUR)$", symbol):
        return "Crypto"
    return "Stocks"


def assumed_spread(symbol: str, price: float) -> float:
    return float(price) * SPREAD_SHARE[category_of(symbol)]


def valid(symbol: str) -> bool:
    return bool(VALID.match(symbol or ""))


def digits_for(price: float) -> int:
    p = abs(float(price))
    return 2 if p >= 100 else 3 if p >= 20 else 5 if p >= 0.5 else 6


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = df.set_index("timestamp").resample(rule, label="left", closed="left")
    return g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(
        subset=["open"]).reset_index()


class YahooCharts:
    def __init__(self, downloader=None, clock=time.time):
        if downloader is None:
            from app.market.providers.yahoo_provider import _download
            downloader = _download
        self.dl, self.clock = downloader, clock
        self.cache: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
        self.lock = threading.Lock()

    def _get(self, symbol: str, interval: str, period: str) -> pd.DataFrame:
        ttl = 300 if interval == "1d" else 60
        key = (symbol, interval)
        with self.lock:
            hit = self.cache.get(key)
        if hit and self.clock() - hit[0] < ttl:
            return hit[1]
        df = self.dl(symbol, interval, period)
        with self.lock:
            self.cache[key] = (self.clock(), df)
            if len(self.cache) > 200:
                for k in sorted(self.cache, key=lambda k: self.cache[k][0])[:50]:
                    del self.cache[k]
        return df

    def frames(self, symbol: str, now: pd.Timestamp | None = None) -> tuple[dict, dict]:
        """(closed candles per timeframe, all candles per timeframe including the forming one)."""
        now = now or pd.Timestamp.now(tz="UTC")
        full: dict[str, pd.DataFrame] = {}
        errors = []
        for tf, (interval, period) in YAHOO_TF.items():
            try:
                full[tf] = self._get(symbol, interval, period)
            except Exception as e:
                errors.append(str(e))
        if "1h" in full:
            full["4h"] = _resample(full["1h"], "4h")
        if not full:
            raise LookupError(errors[0] if errors else f"no data for {symbol}")
        step = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}
        closed = {tf: df[df["timestamp"] + pd.Timedelta(step[tf]) <= now].reset_index(drop=True)
                  for tf, df in full.items()}
        return closed, full


YAHOO = YahooCharts()
