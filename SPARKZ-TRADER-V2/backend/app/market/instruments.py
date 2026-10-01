"""
Instrument specifications: contract size, typical costs, margin and data-source symbols.

Prices are in the quote currency; P&L is reported in USD (USDJPY P&L is divided
by the USDJPY price). Costs are typical retail figures, not any particular
broker's; a live provider's quoted spread replaces the default when available,
and execution.* settings override everything for stress tests.

Instruments differ: gold, FX and crypto have very different price scales,
volatility and trading hours. Nothing here assumes they behave alike.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    contract_size: float       # units per 1.00 lot
    point: float
    spread: float              # typical spread, price units
    slippage: float            # typical adverse slippage per fill, price units
    margin_rate: float         # margin / notional (0.01 = 1:100 leverage)
    quote_ccy: str             # "USD" or "JPY"
    yahoo: str                 # Yahoo symbol (delayed public data)
    broker: str                # OANDA instrument name
    trades_24_7: bool = False
    min_lot: float = 0.01
    lot_step: float = 0.01
    note: str = ""

    def usd_per_price_unit(self, lots: float, price: float) -> float:
        per_unit = lots * self.contract_size
        return per_unit / price if self.quote_ccy == "JPY" else per_unit

    def notional_usd(self, lots: float, price: float) -> float:
        if self.symbol.startswith("USD"):
            return lots * self.contract_size
        return lots * self.contract_size * price

    def margin_usd(self, lots: float, price: float) -> float:
        return self.notional_usd(lots, price) * self.margin_rate

    def round_lot(self, lots: float) -> float:
        return max(self.min_lot, round(round(lots / self.lot_step) * self.lot_step, 8))


INSTRUMENTS: dict[str, Instrument] = {
    "XAUUSD": Instrument("XAUUSD", 100, 0.01, 0.30, 0.05, 0.01, "USD", "GC=F", "XAU_USD",
                         note="Yahoo has no spot gold: GC=F (COMEX futures) is used as a proxy."),
    "EURUSD": Instrument("EURUSD", 100_000, 0.00001, 0.00012, 0.00002, 0.01, "USD", "EURUSD=X", "EUR_USD"),
    "GBPUSD": Instrument("GBPUSD", 100_000, 0.00001, 0.00015, 0.00003, 0.01, "USD", "GBPUSD=X", "GBP_USD"),
    "USDJPY": Instrument("USDJPY", 100_000, 0.001, 0.015, 0.003, 0.01, "JPY", "USDJPY=X", "USD_JPY"),
    "BTCUSD": Instrument("BTCUSD", 1, 0.01, 20.0, 10.0, 0.5, "USD", "BTC-USD", "BTC_USD", trades_24_7=True,
                         note="Crypto CFDs typically carry 1:2 leverage."),
}


def get_instrument(symbol: str) -> Instrument:
    try:
        return INSTRUMENTS[symbol.upper()]
    except KeyError:
        raise ValueError(f"unknown symbol {symbol!r}; supported: {', '.join(INSTRUMENTS)}") from None
