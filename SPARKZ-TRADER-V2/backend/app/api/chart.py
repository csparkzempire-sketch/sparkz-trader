"""Chart: candles of the robot's market, any market the MT5 bridge watches (`--watch`), or any Yahoo Finance
symbol (forex, commodities, index futures, stocks, crypto; app/market/catalog.py), with the strategy
labels (BOS / CHoCH, swings, sweeps, setups, levels, zones, trendlines) and the assistant's plan. Read-only."""

from __future__ import annotations

import math
import threading

from fastapi import APIRouter, HTTPException, Query

from app.api.state import runner
from app.assistant.chart import analyse_chart
from app.assistant.live import gather_frames
from app.assistant.structure_alerts import ALERTS
from app.assistant.context import TF_MINUTES
from app.backtest.metrics import clean
from app.market.catalog import CATALOG, NAMES, YAHOO, assumed_spread, category_of, digits_for, valid
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
    return {"robot_symbol": robot, "symbols": names, "watched": watched, "timeframes": list(CHART_TFS),
            "catalog": {cat: [{"symbol": s, "name": n} for s, n in items] for cat, items in CATALOG.items()},
            "catalog_note": "Yahoo Finance: free, delayed about 10-20 min for stocks and futures; chart only, never traded"}


@router.get("")
def chart(symbol: str = Query("XAUUSD", max_length=20), tf: str = Query("1h"), bars: int = Query(300, ge=60, le=1000),
          source: str = Query("auto", pattern="^(auto|mt5|yahoo)$")):
    if tf not in CHART_TFS:
        raise HTTPException(422, f"timeframe must be one of {', '.join(CHART_TFS)}")
    symbol = symbol.upper()
    r = runner()
    display = None
    feed = "MT5"
    if source == "yahoo" or (source == "auto" and not WATCH.has(symbol) and symbol != r.s.market.symbol.upper()):
        if not valid(symbol):
            raise HTTPException(422, f"not a market symbol: {symbol!r}")
        try:
            frames, full = YAHOO.frames(symbol)
        except LookupError as e:
            raise HTTPException(404, f"Yahoo has no data for {symbol}: {e}")
        if tf not in frames or not len(full.get(tf, [])):
            raise HTTPException(404, f"Yahoo has no {tf} candles for {symbol}")
        last_price = float(full[tf]["close"].iloc[-1])
        display, tick, feed = full[tf], None, "YAHOO"
        spread, digits = assumed_spread(symbol, last_price), digits_for(last_price)
    elif WATCH.has(symbol):
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
        feed = "MT5" if r.info.kind == "BRIDGE" else r.info.kind          # the robot's own feed (MOCK in tests)
        with r.lock:
            frames, spread, digits = _robot_frames(symbol)
        tick = None
    else:
        raise HTTPException(404, f"no MT5 data for {symbol}: start the MT5 bridge with --watch {symbol}")
    last = tuple(str(df["timestamp"].iloc[-1]) if len(df) else "" for df in frames.values())
    key = (symbol, tf, bars, feed, last)
    with _lock:
        hit = _cache.get((symbol, tf, bars, feed))
    if hit and hit[0] == key:
        out = dict(hit[1])
    else:
        out = analyse_chart(frames, tf, spread, bars, display, digits, r.s.assistant.min_confidence)
        with _lock:
            _cache[(symbol, tf, bars, feed)] = (key, out)
            if len(_cache) > 300:
                for k in list(_cache)[:100]:
                    del _cache[k]
        out = dict(out)
    if display is not None and len(display):                     # the forming candle changes between closes
        from app.assistant.chart import candles_payload
        tail = candles_payload(display.tail(1), digits)
        if out.get("candles") and tail and tail[0]["time"] >= out["candles"][-1]["time"]:
            out["candles"] = [c for c in out["candles"] if c["time"] < tail[0]["time"]] + tail
    out["symbol"] = symbol
    out["feed"] = feed
    out["name"] = NAMES.get(symbol)
    out["category"] = category_of(symbol) if feed == "YAHOO" else None
    out["spread_assumed"] = feed == "YAHOO"
    out["alerts"] = [a for a in ALERTS.recent if a["symbol"] == symbol][-10:][::-1]
    out["alert_timeframes"] = list(ALERTS.timeframes)
    if feed != "YAHOO" and symbol == r.s.market.symbol.upper():   # the robot's own trades on its market
        try:
            from app.api.trades import trades
            h = trades(mode=r.s.execution.mode)
            out["robot_trades"] = h["trades"][:100]
            ob = h.get("open_trade")
            out["robot_open"] = None if not ob else {k: ob.get(k) for k in (
                "direction", "average_entry", "total_lots", "opened_at", "floating_pnl", "target_price", "stop_price")}
        except Exception:
            out["robot_trades"], out["robot_open"] = [], None
    out["robot_symbol"] = r.s.market.symbol.upper()
    out["tick"] = tick
    out["seconds_per_candle"] = TF_MINUTES[tf] * 60
    return clean(out)
