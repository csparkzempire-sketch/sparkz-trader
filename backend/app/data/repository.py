"""
Persistence layer for market data.

Two storage paths are supported:
- Flat files under `data/` (parquet/csv) — simple, used by CLI scripts and
  for quick research.
- SQLite via SQLAlchemy (MarketCandle table) — used by the API so multiple
  symbols/timeframes can be queried without re-reading files.

Both paths go through `validate_and_clean` first — nothing raw ever gets
persisted.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import MarketCandle
from app.utils.logging import get_logger, kv

logger = get_logger(__name__, settings.log_level)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _safe_symbol(symbol: str) -> str:
    return symbol.replace("/", "_").replace("=", "_")


def processed_file_path(symbol: str, timeframe: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{_safe_symbol(symbol)}_{timeframe}.parquet"


def save_processed(df: pd.DataFrame, symbol: str, timeframe: str) -> Path:
    """Save a cleaned OHLCV DataFrame to a parquet file under data/."""
    path = processed_file_path(symbol, timeframe)
    df.to_parquet(path, index=False)
    logger.info("Saved processed data %s", kv(symbol=symbol, timeframe=timeframe, rows=len(df), path=str(path)))
    return path


def load_processed(symbol: str, timeframe: str) -> pd.DataFrame:
    path = processed_file_path(symbol, timeframe)
    if not path.exists():
        raise FileNotFoundError(
            f"No processed data found for symbol={symbol!r} timeframe={timeframe!r} at {path}. "
            "Run the data download step first."
        )
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def upsert_candles(session: Session, df: pd.DataFrame, symbol: str, timeframe: str) -> int:
    """
    Replace all stored candles for (symbol, timeframe) with the given DataFrame.
    Simple full-replace strategy — fine at this data scale and avoids subtle
    partial-update bugs. Returns the number of rows written.
    """
    session.execute(
        delete(MarketCandle).where(MarketCandle.symbol == symbol, MarketCandle.timeframe == timeframe)
    )
    rows = [
        MarketCandle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=row.timestamp.to_pydatetime(),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            regime=getattr(row, "regime", None),
        )
        for row in df.itertuples(index=False)
    ]
    session.add_all(rows)
    session.flush()
    logger.info("Upserted candles %s", kv(symbol=symbol, timeframe=timeframe, rows=len(rows)))
    return len(rows)


def query_candles(
    session: Session,
    symbol: str,
    timeframe: str,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    stmt = select(MarketCandle).where(MarketCandle.symbol == symbol, MarketCandle.timeframe == timeframe)
    if start is not None:
        stmt = stmt.where(MarketCandle.timestamp >= start)
    if end is not None:
        stmt = stmt.where(MarketCandle.timestamp <= end)
    stmt = stmt.order_by(MarketCandle.timestamp.asc())
    if limit is not None:
        stmt = stmt.limit(limit)

    rows = session.execute(stmt).scalars().all()
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "regime"])

    return pd.DataFrame(
        [
            {
                "timestamp": r.timestamp,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
                "regime": r.regime,
            }
            for r in rows
        ]
    )
