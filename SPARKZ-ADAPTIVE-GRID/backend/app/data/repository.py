"""
Candle storage in SQLite (MarketCandle).

Downloads and imports are upserted, so the store only grows: Yahoo keeps just
~60 days of 15-minute bars, and saving every download builds a longer history
over time. Reads always go back through the validator.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert

from app.data.validator import validate
from app.models.database import MarketCandle, session_factory


def save_candles(df: pd.DataFrame, symbol: str, timeframe: str, source: str, db=None) -> int:
    if df.empty:
        return 0
    rows = [
        {"symbol": symbol, "timeframe": timeframe, "ts": r.timestamp.to_pydatetime(), "open": r.open,
         "high": r.high, "low": r.low, "close": r.close, "volume": float(r.volume or 0.0),
         "spread": (None if pd.isna(getattr(r, "spread", None)) else float(r.spread)) if "spread" in df.columns else None,
         "source": source}
        for r in df.itertuples(index=False)
    ]
    Session = session_factory(db)
    with Session() as s, s.begin():
        for i in range(0, len(rows), 500):
            stmt = insert(MarketCandle).values(rows[i:i + 500])
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "timeframe", "ts"],
                set_={c: stmt.excluded[c] for c in ["open", "high", "low", "close", "volume", "spread", "source"]},
            )
            s.execute(stmt)
    return len(rows)


def load_candles(symbol: str, timeframe: str, start: str | None = None, end: str | None = None, db=None) -> pd.DataFrame:
    Session = session_factory(db)
    q = select(MarketCandle).where(MarketCandle.symbol == symbol, MarketCandle.timeframe == timeframe)
    if start:
        q = q.where(MarketCandle.ts >= pd.Timestamp(start, tz="UTC").to_pydatetime())
    if end:
        q = q.where(MarketCandle.ts < pd.Timestamp(end, tz="UTC").to_pydatetime())
    with Session() as s:
        rows = s.execute(q.order_by(MarketCandle.ts)).scalars().all()
    df = pd.DataFrame([{"timestamp": r.ts, "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                        "volume": r.volume, "spread": r.spread} for r in rows])
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "spread"])
    if df["spread"].isna().all():
        df = df.drop(columns="spread")
    return validate(df, timeframe)[0]


def candle_inventory(db=None) -> list[dict]:
    Session = session_factory(db)
    with Session() as s:
        rows = s.execute(select(MarketCandle.symbol, MarketCandle.timeframe, func.count(), func.min(MarketCandle.ts),
                                func.max(MarketCandle.ts), func.max(MarketCandle.source))
                         .group_by(MarketCandle.symbol, MarketCandle.timeframe)).all()
    return [{"symbol": r[0], "timeframe": r[1], "bars": r[2], "first": r[3].isoformat() if r[3] else None,
             "last": r[4].isoformat() if r[4] else None, "source": r[5]} for r in rows]


def delete_candles(symbol: str, timeframe: str, db=None) -> None:
    Session = session_factory(db)
    with Session() as s, s.begin():
        s.execute(delete(MarketCandle).where(MarketCandle.symbol == symbol, MarketCandle.timeframe == timeframe))
