"""
Order intents and fills.

The strategy never calls a broker or an executor. It returns OrderIntents,
descriptions of what it wants; the robot passes them through the risk manager
and then to an ExecutionAdapter, which reports back Fills.

  Strategy -> OrderIntent -> RiskManager -> ExecutionAdapter -> Fill -> account & basket
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum

_ids = itertools.count(1)


class IntentType(str, Enum):
    OPEN_BASKET = "OPEN_BASKET"        # first position of a new basket
    ADD_POSITION = "ADD_POSITION"      # grid re-entry into the open basket
    CLOSE_BASKET = "CLOSE_BASKET"      # close every position of the basket


@dataclass
class OrderIntent:
    type: IntentType
    symbol: str
    direction: str                 # the basket's direction (BUY / SELL); a close trades the opposite way
    lots: float
    reason: str
    created_at: datetime
    basket_id: str | None = None
    trigger_price: float | None = None   # mid level that triggered it (None: market)
    close_reason: str | None = None      # TARGET, LOSS_LIMIT, ACCOUNT_DRAWDOWN, MANUAL, END_OF_DATA
    meta: dict = field(default_factory=dict)
    id: int = field(default_factory=lambda: next(_ids))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        d["created_at"] = self.created_at.isoformat()
        return d


@dataclass
class Fill:
    intent_id: int
    type: IntentType
    symbol: str
    direction: str
    lots: float
    price: float             # executed price (costs included)
    reference_mid: float     # mid the fill was priced from
    spread: float
    slippage: float
    commission: float
    time: datetime
    basket_id: str | None
    reason: str
    close_reason: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        d["time"] = self.time.isoformat()
        return d
