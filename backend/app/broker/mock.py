"""
Local, in-memory mock broker.

Fills market orders instantly at the last quote set via `set_quote`, with
the same spread/slippage/commission model the backtester and paper
simulator use (app.backtest.execution). It never talks to a network and
never touches real money, so it works regardless of LIVE_TRADING_ENABLED.

Its purpose is to let order-routing code be written and tested against the
BrokerAdapter interface before any real adapter exists.
"""

from __future__ import annotations

from app.backtest.execution import apply_entry_costs
from app.broker.base import AccountSnapshot, BrokerAdapter, BrokerPosition, OrderRequest, OrderResult
from app.config import Settings
from app.markets.instruments import execution_costs


class MockBroker(BrokerAdapter):
    name = "mock"
    is_live = False

    def __init__(self, cfg: Settings | None = None, starting_balance: float | None = None):
        super().__init__(cfg)
        self.balance = float(starting_balance if starting_balance is not None else self.cfg.initial_capital)
        self._quotes: dict[str, float] = {}
        self._positions: dict[str, tuple[float, float]] = {}  # symbol -> (net_size, avg_price)
        self.order_log: list[OrderResult] = []

    def set_quote(self, symbol: str, mid_price: float) -> None:
        if not mid_price > 0:
            raise ValueError(f"mid_price must be > 0, got {mid_price!r}")
        self._quotes[symbol] = float(mid_price)

    def _submit_order(self, order: OrderRequest) -> OrderResult:
        mid = self._quotes.get(order.symbol)
        if mid is None:
            return self._record(order, "REJECTED", None, f"No quote for {order.symbol}; call set_quote first.")

        fill = apply_entry_costs(mid, order.side, execution_costs(order.symbol, self.cfg))
        signed = order.size if order.side == "BUY" else -order.size
        net, avg = self._positions.get(order.symbol, (0.0, 0.0))

        if net == 0 or (net > 0) == (signed > 0):
            # Opening or adding: size-weighted average entry.
            new_net = net + signed
            new_avg = (abs(net) * avg + abs(signed) * fill) / abs(new_net)
        else:
            # Reducing, closing, or flipping: realize PnL on the closed part.
            closed = min(abs(net), abs(signed))
            direction = 1 if net > 0 else -1
            self.balance += direction * (fill - avg) * closed
            new_net = net + signed
            if new_net == 0:
                new_avg = 0.0
            elif (new_net > 0) == (net > 0):
                new_avg = avg  # partially reduced: remaining size keeps its entry
            else:
                new_avg = fill  # flipped: the leftover is a fresh position at this fill

        self.balance -= self.cfg.commission_per_trade
        if new_net == 0:
            self._positions.pop(order.symbol, None)
        else:
            self._positions[order.symbol] = (new_net, new_avg)
        return self._record(order, "FILLED", fill)

    def _record(self, order: OrderRequest, status: str, price: float | None, reason: str | None = None) -> OrderResult:
        result = OrderResult(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            size=order.size,
            status=status,
            fill_price=price,
            timestamp=self._now(),
            reason=reason,
        )
        self.order_log.append(result)
        return result

    def get_positions(self) -> list[BrokerPosition]:
        return [BrokerPosition(symbol=s, net_size=n, average_price=a) for s, (n, a) in self._positions.items()]

    def get_account(self) -> AccountSnapshot:
        unrealized = sum(
            n * (self._quotes[s] - a) for s, (n, a) in self._positions.items() if s in self._quotes
        )
        return AccountSnapshot(
            balance=self.balance,
            equity=self.balance + unrealized,
            open_positions=len(self._positions),
        )
