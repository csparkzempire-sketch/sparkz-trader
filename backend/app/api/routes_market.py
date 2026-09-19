from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import CandleOut, LatestPriceResponse, MarketDataResponse
from app.data.downloader import DownloadError, download_ohlcv
from app.data.repository import save_processed
from app.data.validator import DataValidationError, validate_and_clean
from app.features.feature_engineering import build_feature_matrix
from app.strategy.regime import classify_regime

router = APIRouter()


@router.get("/{symbol}", response_model=MarketDataResponse)
def get_market_data(
    symbol: str,
    timeframe: str = Query("1h"),
    limit: int = Query(500, ge=1, le=5000),
) -> MarketDataResponse:
    try:
        raw = download_ohlcv(symbol=symbol, timeframe=timeframe)
        clean, _report = validate_and_clean(raw, timeframe=timeframe)
    except (DownloadError, DataValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    featured = build_feature_matrix(clean)
    try:
        featured["regime"] = classify_regime(featured)
    except ValueError:
        featured["regime"] = None

    tail = featured.tail(limit)

    def _safe_float(val):
        import pandas as pd
        if val is None or pd.isna(val):
            return None
        return float(val)

    candles = [
        CandleOut(
            timestamp=row.timestamp,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            regime=None if row.regime is None or str(row.regime) == "<NA>" else str(row.regime),
            ema_20=_safe_float(getattr(row, "ema_20", None)),
            ema_50=_safe_float(getattr(row, "ema_50", None)),
            ema_200=_safe_float(getattr(row, "ema_200", None)),
            rsi=_safe_float(getattr(row, "rsi", None)),
            macd=_safe_float(getattr(row, "macd", None)),
            macd_signal=_safe_float(getattr(row, "macd_signal", None)),
            macd_hist=_safe_float(getattr(row, "macd_hist", None)),
            atr=_safe_float(getattr(row, "atr", None)),
            bb_upper=_safe_float(getattr(row, "bb_upper", None)),
            bb_lower=_safe_float(getattr(row, "bb_lower", None)),
        )
        for row in tail.itertuples(index=False)
    ]
    return MarketDataResponse(symbol=symbol, timeframe=timeframe, count=len(candles), candles=candles)


@router.get("/{symbol}/latest", response_model=LatestPriceResponse)
def get_latest_price(symbol: str, timeframe: str = Query("1h")) -> LatestPriceResponse:
    try:
        raw = download_ohlcv(symbol=symbol, timeframe=timeframe)
        clean, _report = validate_and_clean(raw, timeframe=timeframe)
    except (DownloadError, DataValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    featured = build_feature_matrix(clean)
    try:
        featured["regime"] = classify_regime(featured)
    except ValueError:
        featured["regime"] = None

    last = featured.iloc[-1]
    regime_val = last.get("regime")
    return LatestPriceResponse(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=last["timestamp"],
        close=float(last["close"]),
        regime=None if regime_val is None or pd_isna(regime_val) else str(regime_val),
    )


def pd_isna(val) -> bool:
    import pandas as pd

    return bool(pd.isna(val))
