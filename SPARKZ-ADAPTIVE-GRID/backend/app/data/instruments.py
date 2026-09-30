"""
Instrument specifications: contract size, costs, margin and data source.

All prices are in the instrument's quote currency; P&L is reported in USD.
For USDJPY the quote currency is JPY, so P&L is divided by the USDJPY price.

Cost defaults are typical retail figures, not any particular broker's. They
are deliberately explicit so a backtest can be re-run with a broker's real
numbers (config execution.spread_override / slippage_override /
commission_per_lot_side), or with the per-bar spread from imported MT5 data.

Data source caveat: Yahoo Finance has no spot gold feed. "XAUUSD" downloads
use COMEX gold futures (GC=F) as a proxy. Futures trade at a small premium to
spot and roll between contracts, and their hours differ slightly, so results
from Yahoo data are an approximation of spot XAUUSD. Import broker data
(data/csv_import.py) for a faithful gold test.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    yahoo: str
    contract_size: float      # units per 1.00 lot
    point: float              # smallest price increment shown
    spread: float             # typical spread, price units
    slippage: float           # typical adverse slippage per fill, price units
    margin_rate: float        # margin as a fraction of notional (0.01 = 1:100)
    quote_ccy: str            # "USD" or "JPY"
    min_lot: float = 0.01
    lot_step: float = 0.01
    note: str = ""

    def usd_per_price_unit(self, lots: float, price: float) -> float:
        """USD P&L for a 1.0 price-unit move on `lots` lots."""
        per_unit = lots * self.contract_size
        return per_unit / price if self.quote_ccy == "JPY" else per_unit

    def notional_usd(self, lots: float, price: float) -> float:
        if self.symbol.startswith("USD"):   # USD is the base currency: notional is the units themselves
            return lots * self.contract_size
        return lots * self.contract_size * price

    def margin_usd(self, lots: float, price: float) -> float:
        return self.notional_usd(lots, price) * self.margin_rate

    def round_lot(self, lots: float) -> float:
        steps = round(lots / self.lot_step)
        return max(self.min_lot, steps * self.lot_step)


INSTRUMENTS: dict[str, Instrument] = {
    "XAUUSD": Instrument("XAUUSD", "GC=F", 100, 0.01, 0.30, 0.05, 0.01, "USD",
                         note="Yahoo data is COMEX gold futures (GC=F), a proxy for spot XAUUSD."),
    "EURUSD": Instrument("EURUSD", "EURUSD=X", 100_000, 0.00001, 0.00012, 0.00002, 0.01, "USD"),
    "GBPUSD": Instrument("GBPUSD", "GBPUSD=X", 100_000, 0.00001, 0.00015, 0.00003, 0.01, "USD"),
    "USDJPY": Instrument("USDJPY", "USDJPY=X", 100_000, 0.001, 0.015, 0.003, 0.01, "JPY"),
    "BTCUSD": Instrument("BTCUSD", "BTC-USD", 1, 0.01, 20.0, 10.0, 0.5, "USD",
                         note="Crypto CFDs typically carry 1:2 leverage."),
}


def get_instrument(symbol: str) -> Instrument:
    try:
        return INSTRUMENTS[symbol.upper()]
    except KeyError:
        raise ValueError(f"Unknown symbol {symbol!r}. Supported: {', '.join(INSTRUMENTS)}") from None
