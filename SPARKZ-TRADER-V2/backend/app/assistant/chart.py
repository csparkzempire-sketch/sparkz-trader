"""
Chart analysis: what the strategy detectors see on a candle chart, as drawable objects.

For one symbol and timeframe, on CLOSED candles (each label uses only candles up to its own; nothing is redrawn
with hindsight, except that a swing point is labelled from the candle that confirms it, `n` candles later):

  structure    BOS / CHoCH: the candle that closed through the last swing, and the broken level
  swings       confirmed swing highs and lows, labelled HH / LH / HL / LL
  sweeps       liquidity sweeps (wick through a swing level, close back inside)
  signals      every strategy setup on the visible candles: strategy, side, entry, stop, target
  levels       support / resistance levels at the last candle (clustered swing points, touches)
  zones        fresh supply / demand zones at the last candle
  trendlines   the falling line through the last two lower highs / rising line through the last two higher lows
  analysis     the assistant's multi-timeframe decision for this timeframe, with its plan (entry, stop, target)
               and the same setup's past record on this chart

These are readings of past and present prices, not predictions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.assistant.context import TF_MINUTES, build
from app.assistant.detectors import STRATEGIES, levels, zones
from app.assistant.engine import Assistant

SHORT = {"trend_following": "Trend", "market_structure": "Structure", "support_resistance": "S/R",
         "supply_demand": "S&D", "trendline_break": "TL break", "sr_flip": "S/R flip", "range_trading": "Range",
         "liquidity_sweep": "Sweep", "scalping_momentum": "Scalp"}
MIN_BARS = 60


def _t(ts) -> int:
    return int(pd.Timestamp(ts).timestamp())


def _r(x: float, digits: int) -> float:
    return round(float(x), digits)


def candles_payload(df: pd.DataFrame, digits: int) -> list[dict]:
    return [{"time": _t(r.timestamp), "open": _r(r.open, digits), "high": _r(r.high, digits), "low": _r(r.low, digits),
             "close": _r(r.close, digits)} for r in df.itertuples()]


def analyse_chart(frames: dict[str, pd.DataFrame], tf: str, spread: float, bars: int = 300,
                  display: pd.DataFrame | None = None, digits: int = 2, min_confidence: float = 0.55) -> dict:
    """`frames`: closed candles per timeframe (the assistant reads them all); `display`: the candles to draw on
    `tf`, which may end with the forming candle (default: the closed ones)."""
    closed = frames.get(tf)
    if closed is None or len(closed) < MIN_BARS:
        n = 0 if closed is None else len(closed)
        return {"error": f"not enough {tf} candles yet ({n}, need {MIN_BARS})", "candles": []}
    ctx = build(closed, tf)
    f, st = ctx.f, ctx.st
    m = len(f)
    w0 = max(0, m - bars)
    last = m - 1
    ts = f["timestamp"]
    high, low = f["high"].to_numpy(float), f["low"].to_numpy(float)
    show = display if display is not None and len(display) else closed
    out: dict = {"timeframe": tf, "spread": spread, "digits": digits,
                 "candles": candles_payload(show[pd.to_datetime(show["timestamp"], utc=True) >= ts.iat[w0]], digits),
                 "last_closed": ts.iat[last].isoformat()}

    # ---- structure: BOS / CHoCH with the broken level
    events = []
    for t in range(w0, m):
        ev = st["event"].iat[t]
        if not ev:
            continue
        up = ev.endswith("_UP")
        level = float(st["sh"].iat[t] if up else st["sl"].iat[t])
        conf = ctx.ph_conf if up else ctx.pl_conf
        src = high if up else low
        piv = next((int(k) for k in conf[: t + 1][::-1] if k >= 0 and np.isclose(src[k], level)), None)
        events.append({"time": _t(ts.iat[t]), "type": "CHoCH" if ev.startswith("CHOCH") else "BOS",
                       "direction": "UP" if up else "DOWN", "level": _r(level, digits),
                       "from_time": _t(ts.iat[piv]) if piv is not None else None})
    out["structure"] = events

    # ---- swings: HH / LH / HL / LL, at the pivot candle (known from the confirming candle)
    swings, prev_h, prev_l = [], None, None
    for t in range(m):
        k = ctx.ph_conf[t]
        if k >= 0:
            p = high[k]
            lab = "H" if prev_h is None else ("HH" if p > prev_h else "LH")
            prev_h = p
            if k >= w0:
                swings.append({"time": _t(ts.iat[k]), "kind": "high", "label": lab, "price": _r(p, digits)})
        k = ctx.pl_conf[t]
        if k >= 0:
            p = low[k]
            lab = "L" if prev_l is None else ("HL" if p > prev_l else "LL")
            prev_l = p
            if k >= w0:
                swings.append({"time": _t(ts.iat[k]), "kind": "low", "label": lab, "price": _r(p, digits)})
    out["swings"] = swings

    # ---- liquidity sweeps
    out["sweeps"] = [{"time": _t(ts.iat[t]), "direction": "UP" if ctx.sw["sweep_low"].iat[t] else "DOWN",
                      "price": _r(low[t] if ctx.sw["sweep_low"].iat[t] else high[t], digits)}
                     for t in range(w0, m) if ctx.sw["sweep_low"].iat[t] or ctx.sw["sweep_high"].iat[t]]

    # ---- every strategy setup on the visible candles
    sigs, prev = [], {}                 # a setup that stays on candle after candle is labelled where it starts
    for t in range(max(w0 - 1, 1), m):
        for s in STRATEGIES:
            if s.name == "scalping_momentum" and ctx.minutes > 5:
                continue
            sig = s.detect(ctx, t, spread)
            was, prev[s.name] = prev.get(s.name), (sig.direction if sig else None)
            if sig is None or was == sig.direction or t < w0:
                continue
            sigs.append({"time": _t(ts.iat[t]), "strategy": s.name, "short": SHORT.get(s.name, s.name),
                         "action": "BUY" if sig.direction == 1 else "SELL", "strength": round(sig.strength, 2),
                         "entry": _r(sig.entry, digits), "stop": _r(sig.stop, digits), "target": _r(sig.target, digits),
                         "risk_reward": round(sig.rr, 2), "reasons": sig.reasons[:2]})
    out["signals"] = sigs

    # ---- levels, zones, trendlines at the last closed candle
    out["levels"] = [{"price": _r(p, digits), "touches": c} for p, c in levels(ctx, last)[:6]]
    out["zones"] = [{"kind": "demand" if d == 1 else "supply", "low": _r(zl, digits), "high": _r(zh, digits),
                     "from_time": _t(ts.iat[max(0, k - 1)])} for d, zl, zh, k in zones(ctx, last)[-4:]]
    lines = []
    for kind, pts, ok in (("resistance", ctx.swing_highs(last, 120), lambda a, b: b < a),
                          ("support", ctx.swing_lows(last, 120), lambda a, b: b > a)):
        if len(pts) >= 2 and ok(pts[-2][1], pts[-1][1]):
            (x1, y1), (x2, y2) = pts[-2], pts[-1]
            y_end = y2 + (y2 - y1) / (x2 - x1) * (last - x2)
            lines.append({"kind": kind, "points": [{"time": _t(ts.iat[x1]), "price": _r(y1, digits)},
                                                   {"time": _t(ts.iat[last]), "price": _r(y_end, digits)}]})
    out["trendlines"] = lines

    # ---- the assistant's decision for this timeframe
    try:
        rep = Assistant(min_confidence).analyze({k: v for k, v in frames.items() if len(v) >= MIN_BARS}, spread, tf)
        out["analysis"] = {k: rep.get(k) for k in ("action", "confidence_pct", "higher_timeframe_bias", "reasons",
                                                   "strategy_fit_pct", "plan", "min_confidence_pct", "direction_votes")}
        out["analysis"]["timeframes_read"] = list(rep.get("timeframes", {}))
    except Exception as e:                                       # the chart still draws without it
        out["analysis"] = {"error": f"{type(e).__name__}: {e}"}
    return out


def tf_seconds(tf: str) -> int:
    return TF_MINUTES[tf] * 60
