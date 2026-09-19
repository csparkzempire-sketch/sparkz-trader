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

from app.api.schemas import PaperAccountResponse, PaperPositionOut, PaperStartRequest, PaperTradeOut
from app.paper import state as paper_state
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
    strategy: str = Field("baseline", description="'baseline' or a trained model_id")
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
