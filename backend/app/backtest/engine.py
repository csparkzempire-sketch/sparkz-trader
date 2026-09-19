"""
Event-driven backtest engine.

Execution timing rule (spec section 15): a signal computed from candle N's
close may only execute starting at candle N+1. We implement this by
shifting the signal series forward by one bar before evaluating entries —
so `entry_signal.iloc[i]` reflects what was known as of candle i-1's close,
and any entry at bar i uses bar i's OPEN price (not its close, and
certainly not its high/low, which aren't knowable until the bar finishes).

Stops and targets are checked using the CURRENT bar's high/low only after
that bar has been reached chronologically — never the entry bar's own
high/low relative to information that arrived after entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from app.backtest.execution import ExecutionCosts, apply_entry_costs, apply_exit_costs
from app.backtest.portfolio import OpenPosition, Portfolio
from app.config import Settings, settings
from app.risk.position_sizing import calculate_position_size
from app.risk.risk_manager import RiskManager, RiskState
from app.risk.stops import calculate_stop_and_target
from app.utils.logging import get_logger, kv

logger = get_logger(__name__, settings.log_level)

REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close", "atr"}


@dataclass
class BacktestConfig:
    symbol: str
    timeframe: str
    initial_capital: float = 10_000.0
    risk_per_trade: float = 0.01
    stop_atr_multiplier: float = 2.0
    take_profit_r: float = 2.0
    spread_pips: float = 1.2
    slippage_pips: float = 0.3
    commission_per_trade: float = 0.0
    pip_size: float = 0.0001

    @classmethod
    def from_settings(cls, cfg: Settings, symbol: str, timeframe: str) -> "BacktestConfig":
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            initial_capital=cfg.initial_capital,
            risk_per_trade=cfg.risk_per_trade,
            stop_atr_multiplier=cfg.stop_atr_multiplier,
            take_profit_r=cfg.take_profit_r,
            spread_pips=cfg.spread_pips,
            slippage_pips=cfg.slippage_pips,
            commission_per_trade=cfg.commission_per_trade,
            pip_size=cfg.pip_size,
        )


@dataclass
class BacktestResult:
    portfolio: Portfolio
    config: BacktestConfig
    signal_source: str
    start_time: datetime
    end_time: datetime
    warnings: list[str] = field(default_factory=list)


class BacktestEngine:
    """
    Runs a single strategy (baseline rules OR ML-probability-derived
    signals — the caller supplies the `signal` and optional
    `model_probability` columns) through a realistic, cost-aware,
    next-bar-execution simulation.
    """

    def __init__(self, config: BacktestConfig, risk_manager: RiskManager | None = None):
        self.config = config
        self.risk_manager = risk_manager or RiskManager()
        self.costs = ExecutionCosts(
            spread_pips=config.spread_pips,
            slippage_pips=config.slippage_pips,
            commission_per_trade=config.commission_per_trade,
            pip_size=config.pip_size,
        )

    def run(self, df: pd.DataFrame, signal_col: str = "signal", probability_col: str | None = None) -> BacktestResult:
        missing = REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(f"Backtest input is missing required columns: {sorted(missing)}")
        if signal_col not in df.columns:
            raise ValueError(f"Backtest input is missing signal column '{signal_col}'")
        if not df["timestamp"].is_monotonic_increasing:
            raise ValueError("Input data must be sorted chronologically before backtesting.")

        df = df.reset_index(drop=True)
        # Enforce the execution-timing rule: shift signal forward one bar so
        # bar i's *executable* signal is what was known at bar i-1's close.
        executable_signal = df[signal_col].shift(1)
        executable_probability = df[probability_col].shift(1) if probability_col else None

        portfolio = Portfolio(initial_capital=self.config.initial_capital)
        risk_state = RiskState(equity=self.config.initial_capital, peak_equity=self.config.initial_capital)
        warnings: list[str] = []
        current_day = None

        for i in range(len(df)):
            row = df.iloc[i]
            ts = row["timestamp"]
            bar_day = ts.date() if hasattr(ts, "date") else None
            if current_day is not None and bar_day != current_day:
                risk_state.daily_loss = 0.0
            current_day = bar_day

            # 1. Manage any open position first: check stop/target against
            # THIS bar's high/low (the bar has fully happened by the time we
            # evaluate it in this loop — no look-ahead).
            if portfolio.open_position is not None:
                self._check_exit(portfolio, risk_state, row, ts)

            # 2. Record equity mark-to-market at this bar's close.
            portfolio.record_equity(ts, row["close"])
            risk_state.equity = portfolio.equity(row["close"])
            risk_state.peak_equity = portfolio.peak_equity
            risk_state.bars_since_last_trade += 1

            if portfolio.current_drawdown >= 1.0:
                warnings.append(f"Equity reached zero or below at {ts}; halting further trading.")
                break

            # 3. Consider a new entry using the EXECUTABLE (shifted) signal.
            sig = executable_signal.iloc[i]
            if portfolio.open_position is None and sig in ("BUY", "SELL") and not pd.isna(row.get("atr")):
                self._try_enter(
                    portfolio, risk_state, row, ts, sig,
                    float(executable_probability.iloc[i]) if executable_probability is not None and not pd.isna(executable_probability.iloc[i]) else None,
                )

        # Close any position still open at the end of the data (mark at last close).
        if portfolio.open_position is not None:
            last_row = df.iloc[-1]
            exit_price = apply_exit_costs(last_row["close"], portfolio.open_position.direction, self.costs)
            portfolio.close(last_row["timestamp"], exit_price, reason="END_OF_DATA")
            portfolio.record_equity(last_row["timestamp"], last_row["close"])

        result = BacktestResult(
            portfolio=portfolio,
            config=self.config,
            signal_source=signal_col,
            start_time=df["timestamp"].iloc[0],
            end_time=df["timestamp"].iloc[-1],
            warnings=warnings,
        )
        logger.info(
            "Backtest complete %s",
            kv(symbol=self.config.symbol, trades=len(portfolio.closed_trades), final_equity=round(portfolio.cash, 2)),
        )
        return result

    def _try_enter(self, portfolio: Portfolio, risk_state: RiskState, row, ts, direction: str, probability: float | None) -> None:
        atr_value = row["atr"]
        if pd.isna(atr_value) or atr_value <= 0:
            return

        raw_entry = row["open"]  # next-bar OPEN, per the execution-timing rule
        entry_price = apply_entry_costs(raw_entry, direction, self.costs)

        stop_target = calculate_stop_and_target(
            entry_price=entry_price,
            atr_value=atr_value,
            direction=direction,
            stop_atr_multiplier=self.config.stop_atr_multiplier,
            take_profit_r=self.config.take_profit_r,
        )

        try:
            size_result = calculate_position_size(
                account_equity=risk_state.equity,
                risk_per_trade=self.config.risk_per_trade,
                entry_price=entry_price,
                stop_price=stop_target.stop_price,
            )
        except ValueError:
            return

        check = self.risk_manager.check_new_trade(risk_state, size_result.dollar_risk)
        if not check.allowed:
            return

        portfolio.open(
            OpenPosition(
                direction=direction,
                entry_time=ts,
                entry_price=entry_price,
                stop_price=stop_target.stop_price,
                target_price=stop_target.target_price,
                size=size_result.position_size,
                model_probability=probability,
            )
        )
        risk_state.open_positions += 1
        risk_state.current_exposure += size_result.dollar_risk
        risk_state.bars_since_last_trade = 0

    def _check_exit(self, portfolio: Portfolio, risk_state: RiskState, row, ts) -> None:
        pos = portfolio.open_position
        assert pos is not None

        hit_stop = False
        hit_target = False
        if pos.direction == "BUY":
            hit_stop = row["low"] <= pos.stop_price
            hit_target = row["high"] >= pos.target_price
        else:  # SELL
            hit_stop = row["high"] >= pos.stop_price
            hit_target = row["low"] <= pos.target_price

        # If both stop and target could technically be hit within the same
        # bar, we conservatively assume the stop was hit first (this is the
        # standard conservative assumption in backtesting since intra-bar
        # order is unknown without tick data).
        if hit_stop:
            exit_price = apply_exit_costs(pos.stop_price, pos.direction, self.costs)
            trade = portfolio.close(ts, exit_price, reason="STOP")
            self._settle(risk_state, trade)
        elif hit_target:
            exit_price = apply_exit_costs(pos.target_price, pos.direction, self.costs)
            trade = portfolio.close(ts, exit_price, reason="TARGET")
            self._settle(risk_state, trade)

    def _settle(self, risk_state: RiskState, trade) -> None:
        risk_state.open_positions = max(0, risk_state.open_positions - 1)
        # Remove this trade's risk contribution from current exposure.
        dollar_risk = abs(trade.entry_price - trade.stop_price) * trade.size
        risk_state.current_exposure = max(0.0, risk_state.current_exposure - dollar_risk)
        risk_state.daily_loss += min(trade.pnl, 0.0)
