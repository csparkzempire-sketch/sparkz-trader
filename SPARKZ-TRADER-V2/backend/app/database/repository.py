"""
Persistence. `Repository.attach(robot, run_id)` subscribes to the robot's event
log so every audit entry is written as it happens, along with fills (from
POSITION_* and BASKET_CLOSED events) and completed baskets. Equity is written
once per closed bar by `save_equity`.

A database failure never stops the robot (EventLog isolates listeners); it is
counted and shown in SYSTEM HEALTH.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATA_DIR
from app.database.models import Base, BasketRow, EquityRow, EventRow, FillRow, Run
from app.engine.event_log import LogEntry


def _dt(s):
    if isinstance(s, datetime):
        return s
    return datetime.fromisoformat(s)


class Repository:
    def __init__(self, url: str | None = None):
        if url is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{Path(DATA_DIR) / 'sparkz_v2.db'}"
        self.url = url
        self.engine = create_engine(url, future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self.errors = 0
        self.last_error: str | None = None

    # ----------------------------------------------------------------- health
    def ping(self) -> bool:
        try:
            with self.engine.connect() as c:
                c.execute(text("select 1"))
            return True
        except Exception as e:  # pragma: no cover
            self.last_error = str(e)
            return False

    def _write(self, rows: list) -> None:
        try:
            with self.Session() as s, s.begin():
                s.add_all(rows)
        except Exception as e:
            self.errors += 1
            self.last_error = f"{type(e).__name__}: {e}"

    # ----------------------------------------------------------------- runs
    def start_run(self, kind: str, settings, label: str = "", provider: str = "") -> int:
        with self.Session() as s, s.begin():
            r = Run(kind=kind, label=label, preset=settings.name, symbol=settings.market.symbol,
                    timeframe=settings.market.timeframe, provider=provider, started_at=datetime.now(timezone.utc),
                    settings=settings.model_dump(mode="json"))
            s.add(r)
            s.flush()
            return r.id

    def end_run(self, run_id: int, summary: dict | None = None) -> None:
        with self.Session() as s, s.begin():
            r = s.get(Run, run_id)
            if r:
                r.ended_at, r.summary = datetime.now(timezone.utc), summary

    # ----------------------------------------------------------------- live capture
    def attach(self, robot, run_id: int) -> None:
        def on_entry(e: LogEntry) -> None:
            rows: list = [EventRow(run_id=run_id, seq=e.seq, time=e.time, wall_time=e.wall_time, type=e.type,
                                   message=e.message, data=e.data)]
            f = e.data.get("fill") if e.type in ("POSITION_OPENED", "POSITION_ADDED") else None
            if f:
                rows.append(self._fill_row(run_id, f))
            if e.type == "BASKET_CLOSED":
                b = e.data["basket"]
                rows.append(BasketRow(run_id=run_id, uid=b["uid"], direction=b["direction"],
                                      opened_at=_dt(b["opened_at"]), closed_at=_dt(b["closed_at"]),
                                      positions=b["positions"], pnl=b["pnl"], mae=b["mae"],
                                      close_reason=b["close_reason"], detail=b))
                rows.append(FillRow(run_id=run_id, time=_dt(b["closed_at"]), type="CLOSE_BASKET", basket_id=b["uid"],
                                    direction=b["direction"], lots=b["total_lots"], price=b["exit_price"],
                                    detail={"close_reason": b["close_reason"], "pnl": b["pnl"]}))
            self._write(rows)
        robot.log.listeners.append(on_entry)

    @staticmethod
    def _fill_row(run_id: int, f: dict) -> FillRow:
        return FillRow(run_id=run_id, time=_dt(f["time"]), type=f["type"], basket_id=f.get("basket_id"),
                       direction=f["direction"], lots=f["lots"], price=f["price"], detail=f)

    def save_equity(self, run_id: int, p) -> None:
        self._write([EquityRow(run_id=run_id, time=p.time, equity=p.equity, balance=p.balance, floating=p.floating,
                               positions=p.positions)])

    # ----------------------------------------------------------------- queries
    def runs(self, limit: int = 50) -> list[dict]:
        with self.Session() as s:
            rows = s.scalars(select(Run).order_by(Run.id.desc()).limit(limit)).all()
            return [{"id": r.id, "kind": r.kind, "label": r.label, "preset": r.preset, "symbol": r.symbol,
                     "timeframe": r.timeframe, "provider": r.provider, "started_at": r.started_at.isoformat(),
                     "ended_at": r.ended_at.isoformat() if r.ended_at else None} for r in rows]

    def events(self, run_id: int, limit: int = 500, types: list[str] | None = None) -> list[dict]:
        with self.Session() as s:
            q = select(EventRow).where(EventRow.run_id == run_id)
            if types:
                q = q.where(EventRow.type.in_(types))
            rows = s.scalars(q.order_by(EventRow.seq.desc()).limit(limit)).all()
            return [{"seq": r.seq, "time": r.time.isoformat(), "type": r.type, "message": r.message, "data": r.data}
                    for r in reversed(rows)]

    def baskets(self, run_id: int, limit: int = 500) -> list[dict]:
        with self.Session() as s:
            rows = s.scalars(select(BasketRow).where(BasketRow.run_id == run_id).order_by(BasketRow.id.desc())
                             .limit(limit)).all()
            return [r.detail for r in reversed(rows)]

    def trade_history(self, symbol: str, mode: str, limit: int = 5000) -> dict:
        """Every closed basket of the paper runs on `symbol` with execution mode `mode` (SIMULATION, MT5_DEMO,
        MT5_LIVE), across restarts, oldest first, and the balance the first of those runs started from (the MT5
        balance when it synced, else the configured starting capital)."""
        with self.Session() as s:
            runs = [r for r in s.scalars(select(Run).where(Run.kind == "PAPER", Run.symbol == symbol)
                                         .order_by(Run.id)).all()
                    if (r.settings or {}).get("execution", {}).get("mode", "SIMULATION") == mode]
            ids = [r.id for r in runs]
            if not ids:
                return {"runs": 0, "baskets": [], "starting_balance": None, "currency": "", "first_run": None}
            rows = s.scalars(select(BasketRow).where(BasketRow.run_id.in_(ids)).order_by(BasketRow.closed_at)
                             .limit(limit)).all()
            sync = s.scalars(select(EventRow).where(EventRow.run_id.in_(ids), EventRow.type == "ACCOUNT_SYNCED")
                             .order_by(EventRow.run_id, EventRow.seq).limit(1)).first()
            first_run = runs[0]
            start = (sync.data or {}).get("balance") if sync is not None else None
            if start is None and rows:
                start = (rows[0].detail or {}).get("start_equity")
            if start is None:
                start = (first_run.settings or {}).get("risk", {}).get("initial_capital")
            return {"runs": len(ids), "baskets": [r.detail for r in rows], "starting_balance": start,
                    "currency": (sync.data or {}).get("currency", "") if sync is not None else "",
                    "first_run": first_run.started_at.isoformat()}

    def counts(self, run_id: int) -> dict:
        with self.Session() as s:
            return {"events": s.scalar(select(func.count()).where(EventRow.run_id == run_id)),
                    "baskets": s.scalar(select(func.count()).where(BasketRow.run_id == run_id)),
                    "fills": s.scalar(select(func.count()).where(FillRow.run_id == run_id))}
