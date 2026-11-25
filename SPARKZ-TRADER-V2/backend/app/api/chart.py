"""Chart: candles of the robot's market or any market the MT5 bridge watches (`--watch`), with the strategy
labels (BOS / CHoCH, swings, sweeps, setups, levels, zones, trendlines) and the assistant's plan. Read-only."""

from __future__ import annotations

import math
import threading

from fastapi import APIRouter, HTTPException, Query

from app.api.state import runner
from app.assistant.chart import analyse_chart
from app.assistant.live import gather_frames
from app.assistant.context import TF_MINUTES
from app.backtest.metrics import clean
from app.market.instruments import get_instrument
from app.market.watch import WATCH, WATCH_TFS

router = APIRouter(prefix="/api/chart", tags=["chart"])
CHART_TFS = WATCH_TFS
_cache: dict[tuple, tuple] = {}
_lock = threading.Lock()


def _robot_frames(symbol: str) -> tuple[dict, float, int]:
    """The robot's own feed: what the provider serves, higher timeframes built from finer candles if needed."""
    r = runner()
    frames = {tf: df for tf, df in gather_frames(r.provider, r.s.market.timeframe).items() if tf in CHART_TFS}
    for tf in CHART_TFS:
        if tf in frames:
            continue
        try:
            df = r.provider.get_candles(tf, 1500)
        except Exception:
            continue
        if df is not None and len(df):
            frames[tf] = df
    return frames, r._spread(), _digits(r.robot.strategy.inst.point)


def _digits(point: float) -> int:
    return max(0, min(6, round(-math.log10(point)))) if point > 0 else 2


@router.get("/symbols")
def symbols():
    r = runner()
    robot = r.s.market.symbol.upper()
    watched = WATCH.symbols()
    names = [robot] + [w["symbol"] for w in watched if w["symbol"] != robot]
    return {"robot_symbol": robot, "symbols": names, "watched": watched, "timeframes": list(CHART_TFS)}


@router.get("")
def chart(symbol: str = Query("XAUUSD", max_length=16), tf: str = Query("1h"), bars: int = Query(300, ge=60, le=1000)):
    if tf not in CHART_TFS:
        raise HTTPException(422, f"timeframe must be one of {', '.join(CHART_TFS)}")
    symbol = symbol.upper()
    r = runner()
    display = None
    if WATCH.has(symbol):
        frames = {t: WATCH.frame(symbol, t, closed_only=True) for t in CHART_TFS}
        display = WATCH.frame(symbol, tf)
        tick = WATCH.tick(symbol)
        spread = max(0.0, tick["ask"] - tick["bid"]) if tick else 0.0
        digits = WATCH.digits(symbol)
        if digits is None:
            try:
                digits = _digits(get_instrument(symbol).point)
            except ValueError:
                digits = 5 if len(frames[tf]) and frames[tf]["close"].iloc[-1] < 20 else 3
    elif symbol == r.s.market.symbol.upper():
        with r.lock:
            frames, spread, digits = _robot_frames(symbol)
        tick = None
    else:
        raise HTTPException(404, f"no data for {symbol}: start the MT5 bridge with --watch {symbol}")
    last = tuple(str(df["timestamp"].iloc[-1]) if len(df) else "" for df in frames.values())
    key = (symbol, tf, bars, last)
    with _lock:
        hit = _cache.get((symbol, tf, bars))
    if hit and hit[0] == key:
        out = dict(hit[1])
    else:
        out = analyse_chart(frames, tf, spread, bars, display, digits, r.s.assistant.min_confidence)
        with _lock:
            _cache[(symbol, tf, bars)] = (key, out)
        out = dict(out)
    if display is not None and len(display):                     # the forming candle changes between closes
        from app.assistant.chart import candles_payload
        tail = candles_payload(display.tail(1), digits)
        if out.get("candles") and tail and tail[0]["time"] >= out["candles"][-1]["time"]:
            out["candles"] = [c for c in out["candles"] if c["time"] < tail[0]["time"]] + tail
    out["symbol"] = symbol
    out["robot_symbol"] = r.s.market.symbol.upper()
    out["tick"] = tick
    out["seconds_per_candle"] = TF_MINUTES[tf] * 60
    return clean(out)
