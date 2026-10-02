"""
Broker market-data provider: OANDA v20 REST API, READ-ONLY.

Real bid/ask quotes and broker candles for XAU_USD, EUR_USD, GBP_USD, USD_JPY,
BTC_USD (where the account offers them). This class calls ONLY these endpoints:

  GET /v3/accounts/{account}/pricing?instruments=...      latest bid/ask, tradeable flag
  GET /v3/instruments/{instrument}/candles                mid OHLC candles
  GET /v3/accounts/{account}/instruments?instruments=...  contract details

There is no order, trade or position endpoint here, and no method that could
reach one: in V2 a broker provides market information only.

Credentials come from the environment, never from code, config files, the
frontend or logs:
  OANDA_API_TOKEN     personal access token (a practice account's token works)
  OANDA_ACCOUNT_ID    account id
  OANDA_ENVIRONMENT   "practice" (default) or "live" (still market data only)
The token is sent only in the Authorization header and is never logged.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd

from app.market.instruments import get_instrument
from app.market.providers.base import (MarketDataProvider, MarketStatus, ProviderError, ProviderInfo, SymbolInfo,
                                       Tick)

HOSTS = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}
GRANULARITY = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30", "1h": "H1", "4h": "H4"}


class BrokerProvider(MarketDataProvider):
    def __init__(self, symbol: str = "XAUUSD", timeframe: str = "15m", token: str | None = None,
                 account_id: str | None = None, environment: str | None = None, client=None):
        self.instrument = get_instrument(symbol)
        self.timeframe = timeframe
        self._token = token or os.getenv("OANDA_API_TOKEN", "")
        self.account_id = account_id or os.getenv("OANDA_ACCOUNT_ID", "")
        env = (environment or os.getenv("OANDA_ENVIRONMENT", "practice")).lower()
        if env not in HOSTS:
            raise ProviderError("OANDA_ENVIRONMENT must be 'practice' or 'live'")
        self.base = HOSTS[env]
        if not self._token or not self.account_id:
            raise ProviderError("Broker provider needs OANDA_API_TOKEN and OANDA_ACCOUNT_ID in the environment")
        if client is None:
            import httpx

            client = httpx.Client(timeout=10.0)
        self._client = client
        self._tradeable: bool | None = None

    def __repr__(self) -> str:  # never show the token
        return f"BrokerProvider(OANDA {self.base}, {self.instrument.symbol}, token=***)"

    def provider_symbol(self) -> str:
        return self.instrument.broker

    def info(self) -> ProviderInfo:
        return ProviderInfo("OANDA v20 (market data only)", "BROKER", live=True,
                            notes=["real broker bid/ask; no orders are ever sent"])

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            r = self._client.get(self.base + path, params=params,
                                 headers={"Authorization": f"Bearer {self._token}"})
        except Exception as exc:
            raise ProviderError(f"broker request failed: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise ProviderError(f"broker returned HTTP {r.status_code} for {path.split('?')[0]}")
        return r.json()

    def get_latest_tick(self) -> Tick:
        data = self._get(f"/v3/accounts/{self.account_id}/pricing", {"instruments": self.instrument.broker})
        try:
            p = data["prices"][0]
            bid, ask = float(p["bids"][0]["price"]), float(p["asks"][0]["price"])
            t = pd.Timestamp(p["time"]).tz_convert("UTC").to_pydatetime()
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError("unexpected pricing response") from exc
        self._tradeable = bool(p.get("tradeable", True))
        return Tick(self.instrument.symbol, t, bid, ask)

    def get_candles(self, timeframe: str, count: int) -> pd.DataFrame:
        data = self._get(f"/v3/instruments/{self.instrument.broker}/candles",
                         {"granularity": GRANULARITY[timeframe], "count": min(count + 1, 5000), "price": "M"})
        rows = [{"timestamp": pd.Timestamp(c["time"]).tz_convert("UTC"), "open": float(c["mid"]["o"]),
                 "high": float(c["mid"]["h"]), "low": float(c["mid"]["l"]), "close": float(c["mid"]["c"]),
                 "volume": float(c.get("volume", 0))}
                for c in data.get("candles", []) if c.get("complete")]      # closed candles only
        return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]).tail(count) \
            .reset_index(drop=True)

    def get_history(self, timeframe: str, start: datetime, end: datetime | None = None,
                    max_pages: int = 2000, progress=None) -> tuple[pd.DataFrame, dict]:
        """Closed candles from `start` to `end` (default: now), paged 5000 at a time.

        Requests bid/ask candles ("BA") and stores the mid OHLC. It also returns statistics of the
        candle-close spread, so the backtest's spread assumption can be checked against the broker."""
        end = end or datetime.now(timezone.utc)
        cur = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else pd.Timestamp(end, tz="UTC")
        rows, spreads, pages = [], [], 0
        while cur < end_ts and pages < max_pages:
            data = self._get(f"/v3/instruments/{self.instrument.broker}/candles",
                             {"granularity": GRANULARITY[timeframe], "from": cur.isoformat(), "count": 5000,
                              "price": "BA", "includeFirst": "false" if rows else "true"})
            pages += 1
            batch = [c for c in data.get("candles", []) if c.get("complete")]
            if not batch:
                break
            for c in batch:
                t = pd.Timestamp(c["time"]).tz_convert("UTC")
                if t >= end_ts:
                    break
                b, a = c["bid"], c["ask"]
                mid = {k: (float(b[k]) + float(a[k])) / 2 for k in ("o", "h", "l", "c")}
                rows.append({"timestamp": t, "open": mid["o"], "high": mid["h"], "low": mid["l"],
                             "close": mid["c"], "volume": float(c.get("volume", 0))})
                spreads.append(float(a["c"]) - float(b["c"]))
            last = pd.Timestamp(batch[-1]["time"]).tz_convert("UTC")
            if last <= cur:
                break
            cur = last
            if progress:
                progress(pages, cur)
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        sp = pd.Series(spreads, dtype=float)
        stats = {"pages": pages, "candles": len(df),
                 "spread_median": float(sp.median()) if len(sp) else None,
                 "spread_p90": float(sp.quantile(0.9)) if len(sp) else None,
                 "spread_p99": float(sp.quantile(0.99)) if len(sp) else None}
        return df, stats

    def get_symbol_info(self) -> SymbolInfo:
        data = self._get(f"/v3/accounts/{self.account_id}/instruments", {"instruments": self.instrument.broker})
        i = self.instrument
        try:
            d = data["instruments"][0]
            margin = float(d.get("marginRate", i.margin_rate))
            point = 10 ** -int(d.get("displayPrecision", 2))
        except (KeyError, IndexError, ValueError):
            margin, point = i.margin_rate, i.point
        return SymbolInfo(i.symbol, i.broker, i.contract_size, point, i.min_lot, i.lot_step, margin, i.quote_ccy)

    def get_market_status(self) -> MarketStatus:
        now = datetime.now(timezone.utc)
        if self._tradeable is None:
            try:
                self.get_latest_tick()
            except ProviderError as exc:
                return MarketStatus(False, str(exc), now)
        return MarketStatus(bool(self._tradeable), "broker reports tradeable" if self._tradeable
                            else "broker reports not tradeable", now)
