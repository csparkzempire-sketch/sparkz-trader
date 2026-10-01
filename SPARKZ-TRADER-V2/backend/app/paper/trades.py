"""Trade ledger: every simulated fill, in order (positions opened, added, closed)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.execution.order_intent import Fill


@dataclass
class TradeLedger:
    fills: list[Fill] = field(default_factory=list)
    keep: int | None = None

    def record(self, fill: Fill) -> None:
        self.fills.append(fill)
        if self.keep and len(self.fills) > self.keep:
            del self.fills[: len(self.fills) - self.keep]

    def to_list(self, last: int | None = None) -> list[dict]:
        rows = self.fills[-last:] if last else self.fills
        return [f.to_dict() for f in rows]
