"""Completed baskets and their positions."""

from __future__ import annotations

from fastapi import APIRouter

from app.models.database import BasketPosition, CompletedBasket, session_factory

router = APIRouter(prefix="/baskets", tags=["baskets"])


@router.get("/backtest/{backtest_id}")
def backtest_baskets(backtest_id: int):
    with session_factory()() as s:
        rows = s.query(CompletedBasket).filter_by(backtest_id=backtest_id).order_by(CompletedBasket.opened_at).all()
        pos = s.query(BasketPosition).filter_by(backtest_id=backtest_id).all()
    by = {}
    for p in pos:
        by.setdefault(p.basket_uid, []).append({"seq": p.seq, "lots": p.lots, "entry_price": p.entry_price,
                                                 "entry_time": p.entry_time.isoformat()})
    return [{"uid": b.basket_uid, "direction": b.direction, "opened_at": b.opened_at.isoformat(),
             "closed_at": b.closed_at.isoformat(), "positions": b.positions, "total_lots": b.total_lots,
             "avg_entry": b.avg_entry, "exit_price": b.exit_price, "pnl": b.pnl, "pnl_pct": b.pnl_pct, "mae": b.mae,
             "mfe": b.mfe, "bars_held": b.bars_held, "regime": b.regime, "vol_regime": b.vol_regime,
             "close_reason": b.close_reason, "entries": sorted(by.get(b.basket_uid, []), key=lambda e: e["seq"])}
            for b in rows]
