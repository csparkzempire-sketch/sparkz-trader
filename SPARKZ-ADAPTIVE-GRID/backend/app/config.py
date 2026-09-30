"""
Strategy and system configuration.

Everything the strategy decides with lives here, so thresholds can change
without touching strategy code. Sources, lowest to highest priority:

1. defaults in these models;
2. a YAML file (config/default.yaml, or one of config/strategies/*.yaml);
3. flat environment variables using the names in .env.example
   (SYMBOL, GRID_ATR_MULTIPLIER, MAX_POSITIONS, ...);
4. explicit overrides passed in code (the API and the comparison lab use this).

Safety rules enforced at load time, not left to the caller:
- live trading is not implemented in version 1: LIVE_TRADING_ENABLED=true
  is rejected (fail closed);
- the multiplier ("martingale") sizing mode is refused unless
  ALLOW_MARTINGALE=true is also set;
- max positions per basket has a hard ceiling in code (HARD_MAX_POSITIONS),
  whatever the config says.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

PROJECT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_DIR / "config"
DATA_DIR = PROJECT_DIR / "data"
REPORTS_DIR = PROJECT_DIR / "reports"

# No configuration can raise this: the grid can never hold more positions than this.
HARD_MAX_POSITIONS = 20


class EntryMode(str, Enum):
    TREND = "TREND"            # trade with the trend (the spec's baseline rules)
    RANGE_FADE = "RANGE_FADE"  # fade Bollinger/RSI extremes, only in a ranging regime


class GridMode(str, Enum):
    PRICE = "PRICE"                          # A: fixed price distance between entries
    ATR = "ATR"                              # B: distance = ATR x multiplier
    SIGNAL_CONFIRMED = "SIGNAL_CONFIRMED"    # C: ATR distance AND the analysis still agrees
    NONE = "NONE"                            # single position per basket (a baseline)


class SizingMode(str, Enum):
    FIXED = "FIXED"            # 1, 1, 1, 1 x base lot
    LINEAR = "LINEAR"          # 1, 2, 3, 4 x base lot
    PYRAMID = "PYRAMID"        # adds only while the basket is in profit (favourable moves)
    MARTINGALE = "MARTINGALE"  # 1, 2, 4, 8 x base lot. HIGH RISK, off by default


class TargetMode(str, Enum):
    FIXED = "FIXED"                # basket P&L >= fixed USD amount
    PERCENT = "PERCENT"            # basket P&L >= % of equity at basket start
    RISK_REWARD = "RISK_REWARD"    # basket P&L >= R x the basket's loss limit
    ATR = "ATR"                    # price moves ATR x multiplier beyond the average entry


class IntrabarMode(str, Enum):
    PESSIMISTIC = "PESSIMISTIC"  # the worse outcome when one bar could hit both stop and target
    OHLC_PATH = "OHLC_PATH"      # O->L->H->C on up bars, O->H->L->C on down bars


class MarketCfg(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "15m"
    start: str | None = None  # ISO date; None = all stored data
    end: str | None = None


class AnalysisCfg(BaseModel):
    ema_fast: int = 20
    ema_slow: int = 50
    ema_trend: int = 200
    rsi_period: int = 14
    atr_period: int = 14
    bb_period: int = 20
    bb_std: float = 2.0
    adx_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    trend_adx_min: float = 20.0      # ADX at or above: trending (if EMAs agree)
    range_adx_max: float = 18.0      # ADX at or below: ranging
    vol_lookback: int = 500          # bars in the trailing ATR% percentile window
    high_vol_percentile: float = 0.85
    low_vol_percentile: float = 0.15
    return_lookback: int = 20


class EntryCfg(BaseModel):
    mode: EntryMode = EntryMode.TREND
    rsi_buy_min: float = 50.0
    rsi_sell_max: float = 50.0
    min_trend_strength: float = 20.0      # ADX
    require_ema_trend_alignment: bool = False  # also require price on the right side of EMA200
    skip_high_volatility: bool = True
    fade_rsi_low: float = 30.0
    fade_rsi_high: float = 70.0


class GridCfg(BaseModel):
    mode: GridMode = GridMode.ATR
    price_step: float = 1.0          # PRICE mode: price units between entries
    step_growth: float = 0.0         # PRICE mode: extra distance added per level (1.0, 2.0, 3.0 ...)
    atr_multiplier: float = 0.5      # ATR / SIGNAL_CONFIRMED modes
    min_bars_between_entries: int = 1

    @field_validator("price_step", "atr_multiplier")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("grid spacing must be positive")
        return v


class BaseLotMode(str, Enum):
    FIXED = "FIXED"                  # base_lot as configured
    ATR_NORMALIZED = "ATR_NORMALIZED"  # base lot sized so a 1-ATR move is worth usd_per_atr, per basket


class SizingCfg(BaseModel):
    mode: SizingMode = SizingMode.FIXED
    base_lot: float = 0.01
    base_lot_mode: BaseLotMode = BaseLotMode.FIXED
    usd_per_atr: float = 10.0        # ATR_NORMALIZED: USD value of a 1-ATR move on the base lot
    martingale_multiplier: float = 2.0
    allow_martingale: bool = False

    @field_validator("base_lot")
    @classmethod
    def _lot(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("base lot must be positive")
        return v


class TargetCfg(BaseModel):
    mode: TargetMode = TargetMode.PERCENT
    fixed_usd: float = 10.0
    percent: float = 0.5           # % of equity at basket start
    risk_reward: float = 1.0       # x the basket loss limit
    atr_multiplier: float = 1.0    # price distance beyond the average entry, in ATRs at basket start


class StopCfg(BaseModel):
    max_basket_loss_percent: float = 1.0     # close the basket at this loss (% of equity at basket start)
    max_basket_bars: int | None = None       # optional time stop

    @field_validator("max_basket_loss_percent")
    @classmethod
    def _stop(cls, v: float) -> float:
        if not 0 < v <= 100:
            raise ValueError("a basket loss limit is required (0-100%)")
        return v


class RiskCfg(BaseModel):
    initial_capital: float = 10_000.0
    risk_per_cycle: float = 0.01              # fraction of equity; the RISK_REWARD target's "1R"
    max_positions: int = 5
    max_daily_loss_percent: float = 3.0       # blocks new baskets and adds for the rest of the UTC day
    max_account_drawdown_percent: float = 10.0  # closes everything and halts until reset
    max_exposure_leverage: float = 10.0       # max notional / equity
    max_margin_usage_percent: float = 50.0
    cooldown_bars_after_loss: int = 8

    @field_validator("max_positions")
    @classmethod
    def _max_pos(cls, v: int) -> int:
        if not 1 <= v <= HARD_MAX_POSITIONS:
            raise ValueError(f"max_positions must be between 1 and {HARD_MAX_POSITIONS}")
        return v


class ExecutionCfg(BaseModel):
    spread_multiplier: float = 1.0
    spread_override: float | None = None      # price units; None = instrument default or per-bar data
    slippage_override: float | None = None
    slippage_multiplier: float = 1.0
    commission_per_lot_side: float = 0.0      # USD per lot per side
    entry_latency_bars: int = 0               # 0 = next bar's open after the signal bar closes
    intrabar_mode: IntrabarMode = IntrabarMode.PESSIMISTIC
    use_bar_spread: bool = True               # use a spread column from imported data when present


class Settings(BaseModel):
    market: MarketCfg = Field(default_factory=MarketCfg)
    analysis: AnalysisCfg = Field(default_factory=AnalysisCfg)
    entry: EntryCfg = Field(default_factory=EntryCfg)
    grid: GridCfg = Field(default_factory=GridCfg)
    sizing: SizingCfg = Field(default_factory=SizingCfg)
    target: TargetCfg = Field(default_factory=TargetCfg)
    stop: StopCfg = Field(default_factory=StopCfg)
    risk: RiskCfg = Field(default_factory=RiskCfg)
    execution: ExecutionCfg = Field(default_factory=ExecutionCfg)
    paper_trading: bool = True
    live_trading_enabled: bool = False
    name: str = "default"

    @model_validator(mode="after")
    def _safety(self) -> "Settings":
        if self.live_trading_enabled:
            raise ValueError("Live trading is not implemented in version 1. LIVE_TRADING_ENABLED must be false.")
        if self.sizing.mode == SizingMode.MARTINGALE and not self.sizing.allow_martingale:
            raise ValueError("MARTINGALE sizing is HIGH RISK and disabled by default. "
                             "Set ALLOW_MARTINGALE=true to test it.")
        if self.sizing.mode == SizingMode.MARTINGALE and self.sizing.martingale_multiplier <= 1:
            raise ValueError("martingale_multiplier must be above 1")
        return self


# Flat names (spec / .env.example) -> nested path.
ENV_KEYS: dict[str, tuple[str, ...]] = {
    "SYMBOL": ("market", "symbol"),
    "TIMEFRAME": ("market", "timeframe"),
    "START": ("market", "start"),
    "END": ("market", "end"),
    "INITIAL_CAPITAL": ("risk", "initial_capital"),
    "ENTRY_MODE": ("entry", "mode"),
    "GRID_MODE": ("grid", "mode"),
    "GRID_PRICE_STEP": ("grid", "price_step"),
    "GRID_STEP_GROWTH": ("grid", "step_growth"),
    "GRID_ATR_MULTIPLIER": ("grid", "atr_multiplier"),
    "MAX_POSITIONS": ("risk", "max_positions"),
    "POSITION_SIZING": ("sizing", "mode"),
    "BASE_LOT": ("sizing", "base_lot"),
    "BASE_LOT_MODE": ("sizing", "base_lot_mode"),
    "USD_PER_ATR": ("sizing", "usd_per_atr"),
    "MARTINGALE_MULTIPLIER": ("sizing", "martingale_multiplier"),
    "ALLOW_MARTINGALE": ("sizing", "allow_martingale"),
    "RISK_PER_CYCLE": ("risk", "risk_per_cycle"),
    "BASKET_TARGET_MODE": ("target", "mode"),
    "BASKET_TARGET_USD": ("target", "fixed_usd"),
    "BASKET_TARGET_PERCENT": ("target", "percent"),
    "BASKET_TARGET_RR": ("target", "risk_reward"),
    "TARGET_ATR_MULTIPLIER": ("target", "atr_multiplier"),
    "MAX_BASKET_LOSS_PERCENT": ("stop", "max_basket_loss_percent"),
    "MAX_BASKET_BARS": ("stop", "max_basket_bars"),
    "MAX_DAILY_LOSS_PERCENT": ("risk", "max_daily_loss_percent"),
    "MAX_ACCOUNT_DRAWDOWN_PERCENT": ("risk", "max_account_drawdown_percent"),
    "MAX_EXPOSURE_LEVERAGE": ("risk", "max_exposure_leverage"),
    "MAX_MARGIN_USAGE_PERCENT": ("risk", "max_margin_usage_percent"),
    "COOLDOWN_BARS_AFTER_LOSS": ("risk", "cooldown_bars_after_loss"),
    "SPREAD_MULTIPLIER": ("execution", "spread_multiplier"),
    "SLIPPAGE_MULTIPLIER": ("execution", "slippage_multiplier"),
    "COMMISSION_PER_LOT_SIDE": ("execution", "commission_per_lot_side"),
    "ENTRY_LATENCY_BARS": ("execution", "entry_latency_bars"),
    "INTRABAR_MODE": ("execution", "intrabar_mode"),
    "PAPER_TRADING": ("paper_trading",),
    "LIVE_TRADING": ("live_trading_enabled",),
    "LIVE_TRADING_ENABLED": ("live_trading_enabled",),
}


def _set(d: dict, path: tuple[str, ...], value: Any) -> None:
    for key in path[:-1]:
        d = d.setdefault(key, {})
    d[path[-1]] = value


def deep_merge(base: dict, extra: Mapping) -> dict:
    out = dict(base)
    for k, v in extra.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, Mapping) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str | Path | None = None, env: Mapping[str, str] | None = None,
                overrides: Mapping | None = None) -> Settings:
    data: dict = {}
    path = Path(path) if path else CONFIG_DIR / "default.yaml"
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    env = os.environ if env is None else env
    for key, dest in ENV_KEYS.items():
        if key in env and env[key] != "":
            _set(data, dest, env[key])
    if overrides:
        data = deep_merge(data, overrides)
    return Settings.model_validate(data)


STRATEGIES_DIR = CONFIG_DIR / "strategies"


def list_presets() -> dict[str, str]:
    """Preset key (file stem) -> display name, for the comparison lab."""
    out = {}
    for p in sorted(STRATEGIES_DIR.glob("*.yaml")):
        out[p.stem] = (yaml.safe_load(p.read_text()) or {}).get("name", p.stem)
    return out


def load_preset(key: str, overrides: Mapping | None = None, env: Mapping[str, str] | None = None) -> Settings:
    """default.yaml, then the preset file, then `overrides` (market, costs, risk shared across a comparison)."""
    path = STRATEGIES_DIR / f"{key}.yaml"
    if not path.exists():
        raise ValueError(f"Unknown strategy preset {key!r}. Available: {', '.join(list_presets())}")
    base = yaml.safe_load((CONFIG_DIR / "default.yaml").read_text()) or {}
    merged = deep_merge(base, yaml.safe_load(path.read_text()) or {})
    if overrides:
        merged = deep_merge(merged, overrides)
    env = {} if env is None else env
    for k, dest in ENV_KEYS.items():
        if k in env and env[k] != "":
            _set(merged, dest, env[k])
    return Settings.model_validate(merged)
