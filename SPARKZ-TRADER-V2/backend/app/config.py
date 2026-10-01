"""
SPARKZ TRADER V2 configuration.

Every threshold the strategy uses lives here, so behaviour changes through
configuration, not code. Sources, lowest to highest priority:

1. defaults in these models;
2. a preset file (config/presets/<name>.yaml), e.g. video_style;
3. flat environment variables (names in ENV_KEYS / .env.example);
4. explicit overrides passed in code (API, parameter lab).

Safety rules enforced when settings are loaded, not left to callers:
- ENVIRONMENT is one of DEVELOPMENT, BACKTEST, PAPER. There is no live environment.
- LIVE_TRADING_ENABLED must be false. Setting it true is rejected (fail closed):
  V2 has no live execution adapter at all.
- MULTIPLIER sizing (1, 2, 4, 8 ...) is HIGH RISK and refused unless
  ALLOW_MULTIPLIER_SIZING=true is also set.
- max positions per basket has a hard ceiling in code (HARD_MAX_POSITIONS).
- every basket must have a loss limit.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Literal, Any, Mapping

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

PROJECT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_DIR / "config"
PRESETS_DIR = CONFIG_DIR / "presets"
DATA_DIR = Path(os.getenv("SPARKZ_V2_DATA_DIR") or PROJECT_DIR / "data")
REPORTS_DIR = Path(os.getenv("SPARKZ_V2_REPORTS_DIR") or PROJECT_DIR / "reports")

HARD_MAX_POSITIONS = 20
TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}


class Environment(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"   # mock market data, everything simulated
    BACKTEST = "BACKTEST"         # historical data replayed through the strategy engine
    PAPER = "PAPER"               # current market data, simulated execution


class ProviderKind(str, Enum):
    MOCK = "MOCK"                 # synthetic prices (development, stress tests)
    HISTORICAL = "HISTORICAL"     # stored candles, replayed
    YAHOO = "YAHOO"               # delayed public data, no credentials (XAUUSD = GC=F futures proxy)
    BROKER = "BROKER"             # broker REST API, market data only (OANDA v20)


class EntryMode(str, Enum):
    TREND = "TREND"               # the baseline rules (EMA20/50, price vs EMA20, RSI)
    RANGE_FADE = "RANGE_FADE"     # fade Bollinger/RSI extremes in a ranging market


class GridMode(str, Enum):
    FIXED = "FIXED"                       # A: fixed price distance
    ATR = "ATR"                           # B: ATR x multiplier
    SIGNAL_CONFIRMED = "SIGNAL_CONFIRMED" # C: ATR distance AND the original thesis still holds
    NONE = "NONE"                         # one position per basket (control)


class SizingMode(str, Enum):
    FIXED = "FIXED"               # 1, 1, 1, 1 x base
    LINEAR = "LINEAR"             # 1, 2, 3, 4 x base
    PYRAMID = "PYRAMID"           # base each time; adds only on FAVOURABLE moves
    MULTIPLIER = "MULTIPLIER"     # 1, 2, 4, 8 x base. HIGH RISK, disabled by default


class TargetMode(str, Enum):
    FIXED_PROFIT = "FIXED_PROFIT"       # basket P&L >= fixed USD
    PERCENT_EQUITY = "PERCENT_EQUITY"   # basket P&L >= % of equity at basket start
    ATR_TARGET = "ATR_TARGET"           # price beyond the average entry by ATR x multiplier
    RISK_MULTIPLE = "RISK_MULTIPLE"     # basket P&L >= R x the basket loss limit


class MarketCfg(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "15m"
    provider: ProviderKind = ProviderKind.MOCK
    poll_seconds: float = 5.0              # paper loop: how often to ask the provider for a tick
    stale_after_seconds: float = 120.0     # no fresh tick for this long: block new entries
    history_bars: int = 600                # candles kept for indicator calculation

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v: str) -> str:
        if v not in TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {', '.join(TIMEFRAMES)}")
        return v

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v: str) -> str:
        return v.upper()


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
    trend_adx_min: float = 20.0
    range_adx_max: float = 18.0
    vol_lookback: int = 500
    high_vol_percentile: float = 0.85
    low_vol_percentile: float = 0.15
    return_lookback: int = 20


class EntryCfg(BaseModel):
    mode: EntryMode = EntryMode.TREND
    rsi_buy_min: float = 50.0
    rsi_sell_max: float = 50.0
    min_trend_strength: float = 20.0          # ADX
    entry_threshold: float = 0.6              # minimum rule-agreement score to act (0-1)
    volatility_filter: bool = True            # no new basket in HIGH_VOLATILITY
    require_ema200_alignment: bool = False
    fade_rsi_low: float = 30.0
    fade_rsi_high: float = 70.0


class GridCfg(BaseModel):
    mode: GridMode = GridMode.ATR
    distance: float = 5.0                     # FIXED: price units between entries (GRID_DISTANCE)
    atr_multiplier: float = 0.5               # ATR / SIGNAL_CONFIRMED (GRID_ATR_MULTIPLIER)
    min_bars_between_adds: int = 0            # 0 = several adds may happen in one fast bar

    @field_validator("distance", "atr_multiplier")
    @classmethod
    def _pos(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("grid spacing must be positive")
        return v


class SizingCfg(BaseModel):
    mode: SizingMode = SizingMode.FIXED
    base_lot: float = 0.01
    multiplier: float = 2.0
    allow_multiplier_sizing: bool = False

    @field_validator("base_lot")
    @classmethod
    def _lot(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("base lot must be positive")
        return v


class TargetCfg(BaseModel):
    mode: TargetMode = TargetMode.FIXED_PROFIT
    fixed_usd: float = 10.0
    percent: float = 0.1
    atr_multiplier: float = 1.0
    risk_multiple: float = 1.0


class RiskCfg(BaseModel):
    initial_capital: float = 10_000.0
    max_positions: int = 5
    max_basket_loss_usd: float | None = None          # absolute USD cap (None: use percent)
    max_basket_loss_percent: float = 2.0              # % of equity at basket start
    max_basket_drawdown_usd: float | None = None      # stop ADDING once floating loss is this deep
    max_account_drawdown_percent: float = 20.0        # close everything and STOP the robot
    max_exposure_leverage: float = 10.0               # notional / equity
    max_margin_usage_percent: float = 50.0
    max_daily_loss_percent: float = 5.0
    close_basket_on_limit: bool = True                # at the basket loss limit: close (True) or only stop adding
    cooldown_bars_after_close: int = 1                # extra bars to wait after the bar a basket closed in
    cooldown_bars_after_loss: int = 8                 # further bars after a losing basket

    @field_validator("max_positions")
    @classmethod
    def _maxpos(cls, v: int) -> int:
        if not 1 <= v <= HARD_MAX_POSITIONS:
            raise ValueError(f"max_positions must be 1..{HARD_MAX_POSITIONS}")
        return v

    @field_validator("max_basket_loss_percent")
    @classmethod
    def _loss(cls, v: float) -> float:
        if not 0 < v <= 100:
            raise ValueError("every basket needs a loss limit (0-100% of equity)")
        return v


class ExecutionCfg(BaseModel):
    spread_override: float | None = None     # price units; None = live/instrument spread
    spread_multiplier: float = 1.0
    slippage: float | None = None            # price units per fill; None = instrument default
    slippage_multiplier: float = 1.0
    commission_per_lot_side: float = 0.0
    delay_ms: int = 0                        # simulated order latency (paper loop)
    # Backtest only: order of a candle's high and low while a basket is open (unknown from OHLC).
    # FAVOURABLE_FIRST is the default because ADVERSE_FIRST lets a grid buy the dip and take the
    # rebound inside one candle, which flatters grids (see the backtest engine docstring).
    intrabar_order: Literal["FAVOURABLE_FIRST", "ADVERSE_FIRST", "RANDOM"] = "FAVOURABLE_FIRST"
    intrabar_seed: int = 7


class Settings(BaseModel):
    environment: Environment = Environment.DEVELOPMENT
    live_trading_enabled: bool = False
    video_style_mode: bool = False
    name: str = "default"
    market: MarketCfg = Field(default_factory=MarketCfg)
    analysis: AnalysisCfg = Field(default_factory=AnalysisCfg)
    entry: EntryCfg = Field(default_factory=EntryCfg)
    grid: GridCfg = Field(default_factory=GridCfg)
    sizing: SizingCfg = Field(default_factory=SizingCfg)
    target: TargetCfg = Field(default_factory=TargetCfg)
    risk: RiskCfg = Field(default_factory=RiskCfg)
    execution: ExecutionCfg = Field(default_factory=ExecutionCfg)

    @model_validator(mode="after")
    def _safety(self) -> "Settings":
        if self.live_trading_enabled:
            raise ValueError("LIVE_TRADING_ENABLED must be false: SPARKZ TRADER V2 has no live execution. "
                             "All orders are simulated.")
        if self.sizing.mode == SizingMode.MULTIPLIER:
            if not self.sizing.allow_multiplier_sizing:
                raise ValueError("MULTIPLIER sizing is HIGH RISK and disabled by default. "
                                 "Set ALLOW_MULTIPLIER_SIZING=true to test it deliberately.")
            if self.sizing.multiplier <= 1:
                raise ValueError("sizing.multiplier must be above 1")
        return self

    @property
    def high_risk(self) -> list[str]:
        """Settings that should be shown as warnings wherever results are shown."""
        out = []
        if self.sizing.mode == SizingMode.MULTIPLIER:
            out.append(f"MULTIPLIER sizing x{self.sizing.multiplier}: exposure grows geometrically while losing")
        if not self.risk.close_basket_on_limit:
            out.append("baskets are NOT closed at their loss limit (adds stop only)")
        return out


ENV_KEYS: dict[str, tuple[str, ...]] = {
    "ENVIRONMENT": ("environment",),
    "LIVE_TRADING_ENABLED": ("live_trading_enabled",),
    "VIDEO_STYLE_MODE": ("video_style_mode",),
    "SYMBOL": ("market", "symbol"),
    "TIMEFRAME": ("market", "timeframe"),
    "MARKET_DATA_PROVIDER": ("market", "provider"),
    "POLL_SECONDS": ("market", "poll_seconds"),
    "STALE_AFTER_SECONDS": ("market", "stale_after_seconds"),
    "ENTRY_MODE": ("entry", "mode"),
    "ENTRY_THRESHOLD": ("entry", "entry_threshold"),
    "VOLATILITY_FILTER": ("entry", "volatility_filter"),
    "GRID_MODE": ("grid", "mode"),
    "GRID_DISTANCE": ("grid", "distance"),
    "GRID_ATR_MULTIPLIER": ("grid", "atr_multiplier"),
    "MIN_BARS_BETWEEN_ADDS": ("grid", "min_bars_between_adds"),
    "POSITION_SIZE_MODE": ("sizing", "mode"),
    "BASE_LOT": ("sizing", "base_lot"),
    "SIZE_MULTIPLIER": ("sizing", "multiplier"),
    "ALLOW_MULTIPLIER_SIZING": ("sizing", "allow_multiplier_sizing"),
    "BASKET_TARGET_MODE": ("target", "mode"),
    "BASKET_TARGET": ("target", "fixed_usd"),
    "BASKET_TARGET_PERCENT": ("target", "percent"),
    "BASKET_TARGET_ATR": ("target", "atr_multiplier"),
    "BASKET_TARGET_RISK_MULTIPLE": ("target", "risk_multiple"),
    "INITIAL_CAPITAL": ("risk", "initial_capital"),
    "MAX_POSITIONS": ("risk", "max_positions"),
    "MAX_BASKET_LOSS": ("risk", "max_basket_loss_usd"),
    "MAX_BASKET_LOSS_PERCENT": ("risk", "max_basket_loss_percent"),
    "MAX_BASKET_DRAWDOWN": ("risk", "max_basket_drawdown_usd"),
    "MAX_ACCOUNT_DRAWDOWN": ("risk", "max_account_drawdown_percent"),
    "MAX_EXPOSURE": ("risk", "max_exposure_leverage"),
    "MAX_MARGIN_USAGE": ("risk", "max_margin_usage_percent"),
    "MAX_DAILY_LOSS_PERCENT": ("risk", "max_daily_loss_percent"),
    "CLOSE_BASKET_ON_LIMIT": ("risk", "close_basket_on_limit"),
    "COOLDOWN": ("risk", "cooldown_bars_after_close"),
    "COOLDOWN_AFTER_LOSS": ("risk", "cooldown_bars_after_loss"),
    "SPREAD_OVERRIDE": ("execution", "spread_override"),
    "SPREAD_MULTIPLIER": ("execution", "spread_multiplier"),
    "SLIPPAGE": ("execution", "slippage"),
    "SLIPPAGE_MULTIPLIER": ("execution", "slippage_multiplier"),
    "COMMISSION_PER_LOT_SIDE": ("execution", "commission_per_lot_side"),
    "INTRABAR_ORDER": "execution.intrabar_order",
    "EXECUTION_DELAY_MS": ("execution", "delay_ms"),
}


def _set(d: dict, path: tuple[str, ...], value: Any) -> None:
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


def deep_merge(base: Mapping, extra: Mapping) -> dict:
    out = dict(base)
    for k, v in extra.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, Mapping) and isinstance(out.get(k), Mapping) else v
    return out


def list_presets() -> dict[str, str]:
    return {p.stem: (yaml.safe_load(p.read_text()) or {}).get("name", p.stem) for p in sorted(PRESETS_DIR.glob("*.yaml"))}


def load_settings(preset: str | None = None, env: Mapping[str, str] | None = None,
                  overrides: Mapping | None = None) -> Settings:
    data: dict = {}
    env = os.environ if env is None else env
    if preset is None and str(env.get("VIDEO_STYLE_MODE", "")).lower() in ("1", "true", "yes"):
        preset = "video_style"
    if preset:
        path = PRESETS_DIR / f"{preset}.yaml"
        if not path.exists():
            raise ValueError(f"unknown preset {preset!r}; available: {', '.join(list_presets())}")
        data = yaml.safe_load(path.read_text()) or {}
    for key, dest in ENV_KEYS.items():
        if env.get(key, "") != "":
            _set(data, dest, env[key])
    if overrides:
        data = deep_merge(data, overrides)
    return Settings.model_validate(data)
