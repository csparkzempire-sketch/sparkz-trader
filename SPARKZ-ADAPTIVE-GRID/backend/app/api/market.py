"""Market data and live analysis."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.backtest.runner import prepare_features
from app.config import load_config
from app.data.downloader import DownloadError, download
from app.data.instruments import INSTRUMENTS
from app.data.repository import candle_inventory, load_candles, save_candles
from app.models.schemas import DownloadRequest
from app.strategy.entry_engine import evaluate_entry

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/instruments")
def instruments():
    return [{"symbol": i.symbol, "data_source": i.yahoo, "contract_size": i.contract_size, "spread": i.spread,
             "slippage": i.slippage, "margin_rate": i.margin_rate, "note": i.note} for i in INSTRUMENTS.values()]


@router.get("/inventory")
def inventory():
    return candle_inventory()


@router.post("/download")
def download_data(req: DownloadRequest):
    try:
        df, info = download(req.symbol, req.timeframe)
    except (DownloadError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from None
    save_candles(df, req.symbol.upper(), req.timeframe, info["source"])
    return info


@router.get("/candles")
def candles(symbol: str = "XAUUSD", timeframe: str = "15m", limit: int = 400):
    s = load_config(env={}, overrides={"market": {"symbol": symbol, "timeframe": timeframe}})
    df = load_candles(symbol.upper(), timeframe)
    if df.empty:
        raise HTTPException(404, f"No stored data for {symbol} {timeframe}. Download it first.")
    f = prepare_features(df, s).tail(max(10, min(limit, 3000)))
    cols = ["timestamp", "open", "high", "low", "close", "ema_fast", "ema_slow", "ema_trend", "bb_upper", "bb_lower",
            "rsi", "atr", "adx", "macd", "macd_signal", "macd_hist", "regime", "vol_regime"]
    out = f[cols].copy()
    out["timestamp"] = out["timestamp"].map(lambda t: t.isoformat())
    return out.astype(object).where(out.notna(), None).to_dict("records")


@router.get("/analysis")
def analysis(symbol: str = "XAUUSD", timeframe: str = "15m"):
    """The latest closed bar as the entry engine sees it."""
    s = load_config(env={}, overrides={"market": {"symbol": symbol, "timeframe": timeframe}})
    df = load_candles(symbol.upper(), timeframe)
    if df.empty:
        raise HTTPException(404, f"No stored data for {symbol} {timeframe}.")
    row = prepare_features(df, s).iloc[-1]
    dec = evaluate_entry(row, s.entry)
    keys = ["close", "ema_fast", "ema_slow", "ema_trend", "rsi", "atr", "atr_pct", "adx", "plus_di", "minus_di",
            "macd_hist", "bb_upper", "bb_lower", "bb_width", "vol_percentile", "ret_recent", "dist_ema_fast_atr"]
    return {"timestamp": row["timestamp"].isoformat(), "regime": row["regime"], "vol_regime": row["vol_regime"],
            "signal": dec.direction, "reasons": dec.reasons,
            "features": {k: (None if row[k] != row[k] else float(row[k])) for k in keys}}
