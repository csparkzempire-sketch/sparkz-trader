"""MT5 bridge endpoints: the bridge script pushes quotes here; with EXECUTION_MODE=MT5_DEMO / MT5_LIVE it also collects the
robot's orders and reports MT5's results (see app/execution/mt5_executor.py)."""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app.execution.mt5_executor import ORDERS
from app.market.providers.base import Tick
from app.market.providers.bridge_provider import STORE
from app.market.watch import WATCH, WATCH_TFS

router = APIRouter(prefix="/api/bridge", tags=["bridge"])
MAX_FUTURE = timedelta(minutes=2)


def _utc(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("times must carry a UTC offset (ISO 8601 with Z or +00:00)")
    return v.astimezone(timezone.utc)


class TickIn(BaseModel):
    time: datetime
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)

    @field_validator("time")
    @classmethod
    def _t(cls, v: datetime) -> datetime:
        return _utc(v)

    @model_validator(mode="after")
    def _spread(self):
        if self.ask < self.bid:
            raise ValueError("ask is below bid")
        return self


class BarIn(BaseModel):
    time: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = 0.0
    spread: float | None = Field(default=None, ge=0)     # price units, as MT5 recorded it on the candle

    @field_validator("time")
    @classmethod
    def _t(cls, v: datetime) -> datetime:
        return _utc(v)

    @model_validator(mode="after")
    def _ohlc(self):
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("inconsistent OHLC")
        return self


class PositionIn(BaseModel):
    ticket: int
    side: str
    volume: float = Field(ge=0)
    price_open: float = Field(ge=0)
    sl: float = 0.0
    profit: float = 0.0


class ApprovedPositionIn(BaseModel):
    """A trade you approved, open in MT5 (magic MAGIC_APPROVED)."""
    ticket: int
    symbol: str = Field(max_length=32)
    side: str = Field(pattern="^(BUY|SELL)$")
    volume: float = Field(ge=0)
    price_open: float = Field(ge=0)
    sl: float = 0.0
    tp: float = 0.0
    profit: float = 0.0
    comment: str = Field(default="", max_length=32)
    price_current: float = 0.0


class AccountIn(BaseModel):
    """The MT5 account as the trading bridge sees it (never the login or password)."""
    demo: bool
    real_allowed: bool = False
    trade_allowed: bool = False
    currency: str = Field(default="", max_length=8)
    balance: float = 0.0
    equity: float = 0.0
    margin_free: float = 0.0
    leverage: int = 0
    positions: list[PositionIn] = Field(default_factory=list, max_length=200)    # SPARKZ positions only
    approved_positions: list[ApprovedPositionIn] = Field(default_factory=list, max_length=200)
    features: list[str] = Field(default_factory=list, max_length=20)     # what this bridge version can do


class ResultIn(BaseModel):
    id: int
    status: str = Field(pattern="^(FILLED|REJECTED)$")
    price: float | None = Field(default=None, gt=0)
    volume: float | None = Field(default=None, ge=0)
    time: datetime | None = None
    commission: float = 0.0
    tickets: list[int] = Field(default_factory=list, max_length=200)
    message: str = Field(default="", max_length=300)

    @field_validator("time")
    @classmethod
    def _t(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _utc(v)


class PushIn(BaseModel):
    symbol: str = Field(min_length=3, max_length=16)
    broker_symbol: str = Field(min_length=1, max_length=32)
    tick: TickIn | None = None
    bars_1m: list[BarIn] = Field(default_factory=list, max_length=5000)
    bars_htf: dict[str, list[BarIn]] = Field(default_factory=dict)      # "1h" / "4h" / "1d" closed candles
    account: AccountIn | None = None                                    # only from a bridge run with --trade

    @field_validator("bars_htf")
    @classmethod
    def _htf(cls, v: dict) -> dict:
        bad = set(v) - {"1h", "4h", "1d"}
        if bad:
            raise ValueError(f"bars_htf timeframes must be 1h, 4h or 1d, not {sorted(bad)}")
        if any(len(x) > 3000 for x in v.values()):
            raise ValueError("at most 3000 candles per timeframe")
        return v


class WatchBarIn(BarIn):
    forming: bool = False                 # the candle still open in MT5 (drawn, never analysed)


class WatchIn(BaseModel):
    """Candles of a market the chart shows (bridge --watch). Market data only."""
    broker_symbol: str = Field(min_length=1, max_length=32)
    digits: int | None = Field(default=None, ge=0, le=8)
    tick: TickIn | None = None
    bars: dict[str, list[WatchBarIn]] = Field(default_factory=dict)

    @field_validator("bars")
    @classmethod
    def _tfs(cls, v: dict) -> dict:
        bad = set(v) - set(WATCH_TFS)
        if bad:
            raise ValueError(f"timeframes must be {', '.join(WATCH_TFS)}, not {sorted(bad)}")
        if any(len(x) > 3000 for x in v.values()):
            raise ValueError("at most 3000 candles per timeframe")
        return v


def _authorise(token: str | None) -> None:
    expected = os.getenv("SPARKZ_BRIDGE_TOKEN", "")
    if not expected:
        raise HTTPException(503, "bridge disabled: set SPARKZ_BRIDGE_TOKEN on the server")
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "invalid bridge token")


@router.post("/push")
def push(body: PushIn, x_bridge_token: str | None = Header(default=None)):
    _authorise(x_bridge_token)
    now = datetime.now(timezone.utc)
    latest = max([b.time for b in body.bars_1m] + [b.time for v in body.bars_htf.values() for b in v]
                 + ([body.tick.time] if body.tick else []), default=None)
    if latest is not None and latest - now > MAX_FUTURE:
        raise HTTPException(422, f"data is {(latest - now).total_seconds() / 60:.0f} min ahead of UTC: "
                                 "check the bridge's --utc-offset (MT5 reports the broker server's time)")
    tick = Tick(body.symbol.upper(), body.tick.time, body.tick.bid, body.tick.ask) if body.tick else None
    bars = [b.model_dump() for b in body.bars_1m]
    htf = {tf: [b.model_dump() for b in v] for tf, v in body.bars_htf.items()}
    if body.account is not None:
        ORDERS.set_account(body.account.model_dump())
    return STORE.push(body.symbol.upper(), body.broker_symbol, tick, bars, htf)


@router.post("/watch")
def watch(body: WatchIn, x_bridge_token: str | None = Header(default=None)):
    _authorise(x_bridge_token)
    now = datetime.now(timezone.utc)
    latest = max([b.time for v in body.bars.values() for b in v] + ([body.tick.time] if body.tick else []), default=None)
    if latest is not None and latest - now > MAX_FUTURE:
        raise HTTPException(422, "data is ahead of UTC: check the bridge's --utc-offset")
    tick = {"time": body.tick.time.isoformat(), "bid": body.tick.bid, "ask": body.tick.ask} if body.tick else None
    out = WATCH.push(body.broker_symbol, tick, {tf: [b.model_dump() for b in v] for tf, v in body.bars.items()},
                     body.digits)
    _structure_alerts(out["symbol"])
    return out


def _structure_alerts(symbol: str) -> None:
    """A new BOS / CHoCH on a watched market goes to the robot's event log (and Telegram, when set up)."""
    from app.api.state import STATE
    from app.assistant.structure_alerts import ALERTS

    def emit(a: dict) -> None:
        r = STATE.runner
        if r is not None:
            with r.lock:
                r.robot.log.add(datetime.fromisoformat(a["time"]), "STRUCTURE", a["message"], {"structure": a})

    try:
        ALERTS.check(symbol, lambda tf: WATCH.frame(symbol, tf, closed_only=True), emit)
    except Exception:                                   # alerts never break the price feed
        pass


@router.get("/orders")
def orders(x_bridge_token: str | None = Header(default=None)):
    """Orders for the trading bridge to execute in MT5 (each is handed out once)."""
    _authorise(x_bridge_token)
    return {"execution": ORDERS.mode if ORDERS.enabled else "SIMULATION",
            "orders": ORDERS.fetch() if ORDERS.enabled else []}


@router.post("/orders/result")
def order_result(body: ResultIn, x_bridge_token: str | None = Header(default=None)):
    _authorise(x_bridge_token)
    r = body.model_dump()
    r["time"] = body.time.isoformat() if body.time else None
    if not ORDERS.report(r):
        raise HTTPException(404, f"no order {body.id} waiting for a result")
    return {"ok": True}


@router.get("/spread")
def spread():
    """The broker's real spread from what the bridge has sent, next to the backtests' assumption."""
    from app.research.scalp import ScalpParams

    return STORE.spread_report(assumed=ScalpParams().spread)


@router.get("/status")
def status():
    return {"enabled": bool(os.getenv("SPARKZ_BRIDGE_TOKEN")), "execution": ORDERS.mode if ORDERS.enabled else
            "SIMULATION", "account": ORDERS.account_report(), **STORE.status()}
