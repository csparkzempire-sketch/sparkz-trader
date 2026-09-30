"""
Paper trading API routes.

Uses the shared simulator/account store in app.paper.state so that the
live-feed scheduler (app.paper.scheduler) and manual API calls operate on
the same accounts. State is in-process for this version — a production
build would persist every mutation to the PaperAccount/PaperPosition/
PaperTrade tables. No live broker integration exists anywhere in this
router or the scheduler it drives.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.schemas import (
    PaperAccountResponse,
    PaperPositionOut,
    PaperRunPosition,
    PaperRunSummary,
    PaperRunTrade,
    PaperStartRequest,
    PaperTradeOut,
)
from app.data.downloader import DownloadError, download_ohlcv
from app.paper import state as paper_state
from app.paper.runner import list_states
from app.paper.scheduler import FeedStatus, paper_feed_scheduler

router = APIRouter()


def _get_account(name: str):
    account = paper_state.get_account(name)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Paper account '{name}' not found. Call POST /paper/start first.")
    return account


@router.post("/start", response_model=PaperAccountResponse)
def start_paper_trading(req: PaperStartRequest) -> PaperAccountResponse:
    account = paper_state.get_or_create_account(req.account_name, req.starting_balance)
    paper_state.simulator.start(account)
    return PaperAccountResponse(
        account_name=req.account_name,
        balance=account.balance,
        equity=account.equity({}),
        is_active=account.is_active,
        open_positions=len(account.open_positions),
    )


@router.post("/stop", response_model=PaperAccountResponse)
async def stop_paper_trading(account_name: str = "default") -> PaperAccountResponse:
    account = _get_account(account_name)
    if paper_feed_scheduler.is_running(account_name):
        paper_feed_scheduler.stop(account_name)
    paper_state.simulator.stop(account)
    return PaperAccountResponse(
        account_name=account_name,
        balance=account.balance,
        equity=account.equity({}),
        is_active=account.is_active,
        open_positions=len(account.open_positions),
    )


@router.get("/account", response_model=PaperAccountResponse)
def get_account(account_name: str = "default") -> PaperAccountResponse:
    account = _get_account(account_name)
    return PaperAccountResponse(
        account_name=account_name,
        balance=account.balance,
        equity=account.equity({}),
        is_active=account.is_active,
        open_positions=len(account.open_positions),
    )


@router.get("/positions", response_model=list[PaperPositionOut])
def get_positions(account_name: str = "default") -> list[PaperPositionOut]:
    account = _get_account(account_name)
    return [
        PaperPositionOut(
            symbol=p.symbol, direction=p.direction, entry_price=p.entry_price,
            stop_price=p.stop_price, target_price=p.target_price, size=p.size, opened_at=p.opened_at,
        )
        for p in account.open_positions.values()
    ]


@router.get("/trades", response_model=list[PaperTradeOut])
def get_trades(account_name: str = "default") -> list[PaperTradeOut]:
    account = _get_account(account_name)
    return [
        PaperTradeOut(
            symbol=t.symbol, direction=t.direction, entry_price=t.entry_price, exit_price=t.exit_price,
            size=t.size, pnl=t.pnl, opened_at=t.opened_at, closed_at=t.closed_at, reason=t.reason,
        )
        for t in account.trade_history
    ]


class FeedStartRequest(BaseModel):
    account_name: str = "default"
    symbol: str = "EURUSD=X"
    timeframe: str = "1h"
    strategy: str = Field("baseline", description="'baseline', 'baseline_long_only', or a trained model_id")
    poll_interval_seconds: float = Field(60.0, gt=0, le=3600)


class FeedStatusResponse(BaseModel):
    account_name: str
    symbol: str
    timeframe: str
    strategy: str
    poll_interval_seconds: float
    running: bool
    last_poll_at: str | None = None
    last_candle_timestamp: str | None = None
    last_signal: str | None = None
    last_error: str | None = None
    ticks_processed: int


def _status_to_response(status: FeedStatus) -> FeedStatusResponse:
    return FeedStatusResponse(
        account_name=status.account_name,
        symbol=status.symbol,
        timeframe=status.timeframe,
        strategy=status.strategy,
        poll_interval_seconds=status.poll_interval_seconds,
        running=status.running,
        last_poll_at=status.last_poll_at.isoformat() if status.last_poll_at else None,
        last_candle_timestamp=status.last_candle_timestamp.isoformat() if status.last_candle_timestamp else None,
        last_signal=status.last_signal,
        last_error=status.last_error,
        ticks_processed=status.ticks_processed,
    )


@router.post("/feed/start", response_model=FeedStatusResponse)
async def start_feed(req: FeedStartRequest) -> FeedStatusResponse:
    account = _get_account(req.account_name)
    if not account.is_active:
        raise HTTPException(status_code=400, detail="Account is not active. Call POST /paper/start first.")
    try:
        status = paper_feed_scheduler.start(
            account, req.account_name, req.symbol, req.timeframe, req.strategy, req.poll_interval_seconds
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _status_to_response(status)


@router.post("/feed/stop")
async def stop_feed(account_name: str = "default") -> dict:
    paper_feed_scheduler.stop(account_name)
    return {"account_name": account_name, "running": False}


@router.get("/feed/status", response_model=FeedStatusResponse)
def feed_status(account_name: str = "default") -> FeedStatusResponse:
    status = paper_feed_scheduler.status(account_name)
    if status is None:
        raise HTTPException(status_code=404, detail=f"No live feed has been started for account '{account_name}'.")
    return _status_to_response(status)


# --- Run-once paper accounts (app.paper.runner) ------------------------------
#
# These are the file-backed accounts driven by `python -m app.cli paper-trade`
# (e.g. once a day from a scheduler), separate from the in-memory accounts
# above. This endpoint only READS them: it never runs a trading step, so
# viewing the dashboard can't change an account.


def _latest_price(symbol: str) -> tuple[float | None, object, str | None]:
    """Most recent 1h close as a live-ish mark. Failure is reported, not raised:
    the page still shows balances without a price."""
    try:
        df = download_ohlcv(symbol, "1h", period="2d")
        last = df.iloc[-1]
        return float(last["close"]), last["timestamp"], None
    except (DownloadError, IndexError) as exc:
        return None, None, str(exc)


@router.get("/runs", response_model=list[PaperRunSummary])
def list_paper_runs() -> list[PaperRunSummary]:
    states = list_states()
    prices = {s.config.symbol: _latest_price(s.config.symbol) for s in states}
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
            log=list(reversed(s.log[-50:])),
        ))
    return out
