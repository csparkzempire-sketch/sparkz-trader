"""
Paper trading simulator.

Simulates account balance, positions, orders, fills, spread, slippage,
stop-loss, take-profit, and PnL — entirely separate from any real broker.
No broker credentials are required or accepted here.

If a future version adds a real broker adapter, it must check
`settings.live_trading_enabled` and fail closed when False (see
app.config.Settings.live_trading_enabled, default False). This module
never places real orders and contains no broker integration code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.backtest.execution import ExecutionCosts, apply_entry_costs, apply_exit_costs
from app.config import Settings, settings
from app.risk.position_sizing import calculate_position_size
from app.risk.risk_manager import RiskManager, RiskState
from app.risk.stops import calculate_stop_and_target
from app.utils.logging import get_logger, kv
from app.utils.time import utc_now

logger = get_logger(__name__)


@dataclass
class PaperPosition:
    symbol: str
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    size: float
    opened_at: datetime


@dataclass
class PaperTradeRecord:
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    opened_at: datetime
    closed_at: datetime
    reason: str


@dataclass
class PaperAccountState:
    balance: float
    is_active: bool = False
    open_positions: dict[str, PaperPosition] = field(default_factory=dict)  # keyed by symbol
    trade_history: list[PaperTradeRecord] = field(default_factory=list)
    risk_state: RiskState = field(default=None)  # set in __post_init__

    def __post_init__(self):
        if self.risk_state is None:
            self.risk_state = RiskState(equity=self.balance, peak_equity=self.balance)

    def equity(self, current_prices: dict[str, float]) -> float:
        unrealized = 0.0
        for symbol, pos in self.open_positions.items():
            price = current_prices.get(symbol)
            if price is None:
                continue
            direction_sign = 1 if pos.direction == "BUY" else -1
            unrealized += direction_sign * (price - pos.entry_price) * pos.size
        return self.balance + unrealized


class PaperTradingSimulator:
    """
    In-memory paper trading simulator. A thin persistence layer (see
    app.database.models.PaperAccount / PaperPosition / PaperTrade) can save
    this state to the DB between calls — this class holds the simulation
    logic itself, decoupled from storage.
    """

    def __init__(self, cfg: Settings | None = None, risk_manager: RiskManager | None = None):
        self.cfg = cfg or settings
        self.risk_manager = risk_manager or RiskManager(self.cfg)
        self.costs = ExecutionCosts(
            spread_pips=self.cfg.spread_pips,
            slippage_pips=self.cfg.slippage_pips,
            commission_per_trade=self.cfg.commission_per_trade,
            pip_size=self.cfg.pip_size,
        )

    def start(self, account: PaperAccountState) -> None:
        account.is_active = True
        logger.info("Paper trading started %s", kv(balance=account.balance))

    def stop(self, account: PaperAccountState) -> None:
        account.is_active = False
        logger.info("Paper trading stopped %s", kv(balance=account.balance, open_positions=len(account.open_positions)))

    def open_position(
        self,
        account: PaperAccountState,
        symbol: str,
        direction: str,
        raw_price: float,
        atr_value: float,
        timestamp: datetime | None = None,
    ) -> PaperPosition | None:
        if not account.is_active:
            raise RuntimeError("Paper account is not active. Call start() first.")
        if symbol in account.open_positions:
            return None  # one position per symbol at a time in v1

        timestamp = timestamp or utc_now()
        entry_price = apply_entry_costs(raw_price, direction, self.costs)
        stop_target = calculate_stop_and_target(
            entry_price=entry_price,
            atr_value=atr_value,
            direction=direction,
            stop_atr_multiplier=self.cfg.stop_atr_multiplier,
            take_profit_r=self.cfg.take_profit_r,
        )
        size_result = calculate_position_size(
            account_equity=account.risk_state.equity,
            risk_per_trade=self.cfg.risk_per_trade,
            entry_price=entry_price,
            stop_price=stop_target.stop_price,
        )
        check = self.risk_manager.check_new_trade(account.risk_state, size_result.dollar_risk)
        if not check.allowed:
            logger.info("Paper trade rejected by risk manager %s", kv(symbol=symbol, reason=check.reason))
            return None

        position = PaperPosition(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_target.stop_price,
            target_price=stop_target.target_price,
            size=size_result.position_size,
            opened_at=timestamp,
        )
        account.open_positions[symbol] = position
        account.risk_state.open_positions += 1
        account.risk_state.current_exposure += size_result.dollar_risk
        logger.info("Paper position opened %s", kv(symbol=symbol, direction=direction, entry=entry_price, size=size_result.position_size))
        return position

    def check_and_close_if_hit(
        self, account: PaperAccountState, symbol: str, high: float, low: float, timestamp: datetime | None = None
    ) -> PaperTradeRecord | None:
        pos = account.open_positions.get(symbol)
        if pos is None:
            return None

        hit_stop = low <= pos.stop_price if pos.direction == "BUY" else high >= pos.stop_price
        hit_target = high >= pos.target_price if pos.direction == "BUY" else low <= pos.target_price

        if not (hit_stop or hit_target):
            return None

        reason = "STOP" if hit_stop else "TARGET"  # conservative: stop wins ties, same as backtest engine
        raw_exit = pos.stop_price if hit_stop else pos.target_price
        return self._close(account, symbol, raw_exit, reason, timestamp)

    def close_position_at_market(
        self, account: PaperAccountState, symbol: str, raw_price: float, timestamp: datetime | None = None
    ) -> PaperTradeRecord | None:
        return self._close(account, symbol, raw_price, "MANUAL_CLOSE", timestamp)

    def _close(
        self, account: PaperAccountState, symbol: str, raw_price: float, reason: str, timestamp: datetime | None
    ) -> PaperTradeRecord:
        pos = account.open_positions.pop(symbol)
        timestamp = timestamp or utc_now()
        exit_price = apply_exit_costs(raw_price, pos.direction, self.costs)
        direction_sign = 1 if pos.direction == "BUY" else -1
        pnl = direction_sign * (exit_price - pos.entry_price) * pos.size

        account.balance += pnl
        account.risk_state.equity = account.balance
        account.risk_state.peak_equity = max(account.risk_state.peak_equity, account.balance)
        account.risk_state.open_positions = max(0, account.risk_state.open_positions - 1)
        dollar_risk = abs(pos.entry_price - pos.stop_price) * pos.size
        account.risk_state.current_exposure = max(0.0, account.risk_state.current_exposure - dollar_risk)
        account.risk_state.daily_loss += min(pnl, 0.0)

        record = PaperTradeRecord(
            symbol=symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            size=pos.size,
            pnl=pnl,
            opened_at=pos.opened_at,
            closed_at=timestamp,
            reason=reason,
        )
        account.trade_history.append(record)
        logger.info("Paper position closed %s", kv(symbol=symbol, reason=reason, pnl=round(pnl, 2)))
        return record
