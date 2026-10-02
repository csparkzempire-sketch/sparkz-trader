"""MT5 bridge endpoints: the bridge script pushes quotes here (market data only)."""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app.market.providers.base import Tick
from app.market.providers.bridge_provider import STORE

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

    @field_validator("time")
    @classmethod
    def _t(cls, v: datetime) -> datetime:
        return _utc(v)

    @model_validator(mode="after")
    def _ohlc(self):
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("inconsistent OHLC")
        return self


class PushIn(BaseModel):
    symbol: str = Field(min_length=3, max_length=16)
    broker_symbol: str = Field(min_length=1, max_length=32)
    tick: TickIn | None = None
    bars_1m: list[BarIn] = Field(default_factory=list, max_length=5000)


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
    latest = max([b.time for b in body.bars_1m] + ([body.tick.time] if body.tick else []), default=None)
    if latest is not None and latest - now > MAX_FUTURE:
        raise HTTPException(422, f"data is {(latest - now).total_seconds() / 60:.0f} min ahead of UTC: "
                                 "check the bridge's --utc-offset (MT5 reports the broker server's time)")
    tick = Tick(body.symbol.upper(), body.tick.time, body.tick.bid, body.tick.ask) if body.tick else None
    bars = [b.model_dump() for b in body.bars_1m]
    return STORE.push(body.symbol.upper(), body.broker_symbol, tick, bars)


@router.get("/status")
def status():
    return {"enabled": bool(os.getenv("SPARKZ_BRIDGE_TOKEN")), **STORE.status()}
