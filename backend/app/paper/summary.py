"""
Summaries of the run-once paper accounts, shared by GET /paper/runs and the
paper-snapshot feed that updates the hosted status page, so both show the
same figures. Read-only: nothing here runs a trading step.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

from app.api.schemas import PaperRunPosition, PaperRunSummary, PaperRunTrade
from app.data.downloader import DownloadError, download_ohlcv
from app.paper.evaluation import evaluate
from app.paper.norms import norms_status
from app.paper.runner import PaperRunState

PriceResult = tuple[float | None, object, str | None]


def latest_price(symbol: str) -> PriceResult:
    """Most recent 1h close (as a live-ish mark) and its UTC time. Failure is
    reported, not raised, so balances still show without a price."""
    try:
        last = download_ohlcv(symbol, "1h", period="2d").iloc[-1]
        return float(last["close"]), pd.Timestamp(last["timestamp"]).tz_convert("UTC"), None
    except (DownloadError, IndexError) as exc:
        return None, None, str(exc)


def summarize(states: list[PaperRunState], price_fn: Callable[[str], PriceResult] = latest_price,
              log_lines: int = 50) -> list[PaperRunSummary]:
    prices = {s.config.symbol: price_fn(s.config.symbol) for s in states}
    out = []
    for s in states:
        c, a = s.config, s.account
        price, price_at, price_error = prices[c.symbol]
        positions = []
        unrealized_total = 0.0
        for p in a.open_positions.values():
            unrealized = stop_d = target_d = None
            if price is not None:
                unrealized = (price - p.entry_price) * p.size * (1 if p.direction == "BUY" else -1)
                unrealized_total += unrealized
                stop_d = (p.stop_price / price - 1) * 100
                target_d = (p.target_price / price - 1) * 100
            positions.append(PaperRunPosition(
                symbol=p.symbol, direction=p.direction, size=p.size, entry_price=p.entry_price,
                stop_price=p.stop_price, target_price=p.target_price, opened_at=p.opened_at,
                unrealized_pnl=unrealized, stop_distance_pct=stop_d, target_distance_pct=target_d,
            ))
        equity = a.balance + unrealized_total
        trades = [
            PaperRunTrade(
                symbol=t.symbol, direction=t.direction, entry_price=t.entry_price, exit_price=t.exit_price,
                size=t.size, pnl=t.pnl, opened_at=t.opened_at, closed_at=t.closed_at, reason=t.reason,
            )
            for t in reversed(a.trade_history)  # newest first
        ]
        out.append(PaperRunSummary(
            account_name=c.account_name, symbol=c.symbol, timeframe=c.timeframe, strategy=c.strategy,
            starting_balance=c.starting_balance, balance=a.balance, equity=equity,
            return_pct=(equity / c.starting_balance - 1) * 100 if c.starting_balance else 0.0,
            last_processed=s.last_processed, latest_price=price, latest_price_at=price_at,
            price_error=price_error, open_positions=positions, closed_trades=trades,
            winning_trades=sum(1 for t in a.trade_history if t.pnl > 0),
            halted=s.halted,
            evaluation=evaluate(s),
            norms=norms_status(s),
            log=list(reversed(s.log[-log_lines:])),
        ))
    return out
