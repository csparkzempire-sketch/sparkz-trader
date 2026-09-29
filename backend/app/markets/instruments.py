"""
Per-instrument market profiles.

Trading costs are quoted in pips, and a pip is not the same size on every
market: 0.0001 on EUR/USD, 0.01 on USD/JPY. A single global PIP_SIZE of
0.0001 applied to USD/JPY makes every spread and slippage charge 100x too
small, which quietly inflates backtest profits. Crypto trades 24/7, so
weekend gaps are real data holes there (not a normal market close) and
annualization must count 365 days, not 252.

Cost precedence for a symbol, highest first:
  1. a per-run override (a Settings copy made with model_copy(update=...),
     e.g. from a CLI flag or test) -- detected via `model_fields_set`
  2. the instrument's profile below
  3. the global Settings values (PIP_SIZE / SPREAD_PIPS / SLIPPAGE_PIPS),
     which now only act as defaults for symbols not listed here

Spread/slippage defaults here are rough, typical retail figures, not
quotes from any particular broker. Override them for your own venue.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.backtest.execution import ExecutionCosts
from app.config import Settings, settings

FX = "fx"
CRYPTO = "crypto"


@dataclass(frozen=True)
class Instrument:
    symbol: str
    display_name: str
    asset_class: str
    pip_size: float  # price units per pip
    spread_pips: float
    slippage_pips: float

    @property
    def trades_24_7(self) -> bool:
        return self.asset_class == CRYPTO


INSTRUMENTS: dict[str, Instrument] = {
    i.symbol: i
    for i in [
        Instrument("EURUSD=X", "EUR/USD", FX, pip_size=0.0001, spread_pips=1.2, slippage_pips=0.3),
        Instrument("GBPUSD=X", "GBP/USD", FX, pip_size=0.0001, spread_pips=1.5, slippage_pips=0.3),
        Instrument("USDJPY=X", "USD/JPY", FX, pip_size=0.01, spread_pips=1.4, slippage_pips=0.3),
        # Crypto "pips" are whole price units: $1 on BTC, $0.10 on ETH.
        Instrument("BTC-USD", "BTC/USD", CRYPTO, pip_size=1.0, spread_pips=15.0, slippage_pips=10.0),
        Instrument("ETH-USD", "ETH/USD", CRYPTO, pip_size=0.1, spread_pips=10.0, slippage_pips=5.0),
    ]
}


def get_instrument(symbol: str) -> Instrument | None:
    return INSTRUMENTS.get(symbol)


@dataclass(frozen=True)
class CostProfile:
    pip_size: float
    spread_pips: float
    slippage_pips: float


def resolve_costs(symbol: str, cfg: Settings | None = None) -> CostProfile:
    cfg = cfg or settings
    inst = get_instrument(symbol)
    overridden = cfg.model_fields_set

    def pick(field: str) -> float:
        if field in overridden or inst is None:
            return getattr(cfg, field)
        return getattr(inst, field)

    return CostProfile(
        pip_size=pick("pip_size"),
        spread_pips=pick("spread_pips"),
        slippage_pips=pick("slippage_pips"),
    )


def execution_costs(symbol: str, cfg: Settings | None = None) -> ExecutionCosts:
    cfg = cfg or settings
    costs = resolve_costs(symbol, cfg)
    return ExecutionCosts(
        spread_pips=costs.spread_pips,
        slippage_pips=costs.slippage_pips,
        commission_per_trade=cfg.commission_per_trade,
        pip_size=costs.pip_size,
    )


def trades_24_7(symbol: str | None) -> bool:
    inst = get_instrument(symbol) if symbol else None
    return bool(inst and inst.trades_24_7)
