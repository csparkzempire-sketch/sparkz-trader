"""
Strategy detectors. Each strategy has two questions, both answered at a context row i from rows 0..i only:

  fit(ctx, i)               0..1: how well the market's current conditions suit this strategy
  detect(ctx, i, spread)    a Signal when the strategy's setup is present on this candle, else None

Strategies:
  trend_following     EMA20 > EMA50 > EMA200 with ADX >= 20 and price above EMA20 (mirrored for shorts)
  market_structure    a break of structure (BOS) or change of character (CHoCH) on this candle
  support_resistance  rejection at a support/resistance level made of several confirmed swing points
  supply_demand       return into a fresh demand/supply zone: the base before an impulsive candle
  trendline_break     close through a trendline drawn through the last two lower highs / higher lows
  sr_flip             a broken level retested from the other side (resistance turned support, or reverse)
  range_trading       ADX < 20 and price at a Bollinger band extreme with RSI stretched: fade to the middle
  liquidity_sweep     a wick through a swing point / Asia extreme with the close back inside
  scalping_momentum   1m/5m only: EMA9/21 cross with a breakout of the last 20 candles, if the move is large
                      enough against the spread

Stops sit beyond the structure that invalidates the idea; targets are 2R unless a level is nearer and still
at least 1.5R away.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np

from app.assistant.context import Context


@dataclass
class Signal:
    strategy: str
    tf: str
    direction: int              # +1 buy, -1 sell
    strength: float             # 0..1
    entry: float
    stop: float
    target: float
    reasons: list[str] = field(default_factory=list)

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def rr(self) -> float:
        return abs(self.target - self.entry) / self.risk if self.risk > 0 else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["action"] = "BUY" if self.direction == 1 else "SELL"
        d["risk_reward"] = round(self.rr, 2)
        for k in ("entry", "stop", "target", "strength"):
            d[k] = round(d[k], 3)
        return d


def _ok(*v) -> bool:
    return all(x is not None and np.isfinite(x) for x in v)


def _clip(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def _plan(ctx: Context, i: int, d: int, stop: float, rr: float = 2.0, level: float | None = None) -> tuple:
    entry = float(ctx.f["close"].iat[i])
    risk = (entry - stop) * d
    if risk <= 0:
        return None
    target = entry + d * rr * risk
    if level is not None and (level - entry) * d >= 1.5 * risk:
        target = min(target, level) if d == 1 else max(target, level)
    return entry, float(stop), float(target)


def levels(ctx: Context, i: int, lookback: int = 300, tol_atr: float = 0.35) -> list[tuple[float, int]]:
    """Support/resistance levels at row i: confirmed swing highs and lows within `lookback` rows, clustered
    when closer than tol_atr x ATR; (price, touches), strongest first."""
    atr = ctx.f["atr"].iat[i]
    if not _ok(atr) or atr <= 0:
        return []
    pts = sorted(p for _, p in ctx.swing_highs(i, lookback) + ctx.swing_lows(i, lookback))
    out: list[list[float]] = []
    for p in pts:
        if out and p - out[-1][0] / out[-1][1] <= tol_atr * atr:
            out[-1][0] += p
            out[-1][1] += 1
        else:
            out.append([p, 1])
    return sorted(((s / c, int(c)) for s, c in out), key=lambda x: -x[1])


# ------------------------------------------------------------------------------------------------- fits
def _trend(ctx, i) -> float:
    r = ctx.f.iloc[i]
    if not _ok(r.adx, r.ema_fast, r.ema_slow):
        return 0.0
    aligned = (r.ema_fast > r.ema_slow) == (r.close > r.ema_fast)
    return _clip((r.adx - 15) / 25) * (1.0 if aligned else 0.5)


def _range(ctx, i) -> float:
    r = ctx.f.iloc[i]
    if not _ok(r.adx):
        return 0.0
    return _clip((25 - r.adx) / 15)


def _breakout(ctx, i) -> float:
    r = ctx.f.iloc[i]
    if not _ok(r.atr, r.atr_mean100, r.hi20, r.lo20):
        return 0.0
    expansion = _clip((r.atr / r.atr_mean100 - 0.9) / 0.6)
    beyond = 1.0 if (r.close > r.hi20 or r.close < r.lo20) else 0.4
    return expansion * beyond


def _near_level(ctx, i) -> float:
    r = ctx.f.iloc[i]
    lv = levels(ctx, i)
    if not lv or not _ok(r.atr):
        return 0.0
    dist = min(abs(r.close - p) for p, _ in lv) / r.atr
    return _clip(1 - dist / 1.5)


def _reversal(ctx, i) -> float:
    ev = ctx.st["event"].iloc[max(0, i - 5):i + 1]
    sw = ctx.sw.iloc[max(0, i - 5):i + 1]
    score = 0.6 * ev.str.startswith("CHOCH").any() + 0.6 * (sw["sweep_low"].any() or sw["sweep_high"].any())
    return _clip(score)


# ---------------------------------------------------------------------------------------------- detectors
def trend_following(ctx: Context, i: int, spread: float) -> Signal | None:
    r = ctx.f.iloc[i]
    if not _ok(r.ema_fast, r.ema_slow, r.ema_trend, r.adx, r.atr) or r.adx < 20:
        return None
    if r.ema_fast > r.ema_slow > r.ema_trend and r.close > r.ema_fast:
        d = 1
    elif r.ema_fast < r.ema_slow < r.ema_trend and r.close < r.ema_fast:
        d = -1
    else:
        return None
    p = _plan(ctx, i, d, r.ema_slow - d * 0.5 * r.atr)
    if p is None:
        return None
    return Signal("trend_following", ctx.tf, d, _clip((r.adx - 20) / 25 + 0.3), *p,
                  [f"EMA20/50/200 stacked {'up' if d == 1 else 'down'}", f"ADX {r.adx:.0f}"])


def market_structure(ctx: Context, i: int, spread: float) -> Signal | None:
    ev = ctx.st["event"].iat[i]
    if not ev:
        return None
    d = 1 if ev.endswith("UP") else -1
    swing = ctx.st["sl"].iat[i] if d == 1 else ctx.st["sh"].iat[i]
    atr = ctx.f["atr"].iat[i]
    if not _ok(swing, atr):
        return None
    p = _plan(ctx, i, d, swing - d * 0.2 * atr)
    if p is None:
        return None
    kind = "break of structure (continuation)" if ev.startswith("BOS") else "change of character (reversal)"
    return Signal("market_structure", ctx.tf, d, 0.6 if ev.startswith("BOS") else 0.5, *p, [kind])


def support_resistance(ctx: Context, i: int, spread: float) -> Signal | None:
    r = ctx.f.iloc[i]
    if not _ok(r.atr, r.lower_wick_share, r.upper_wick_share):
        return None
    lv = [x for x in levels(ctx, i) if x[1] >= 2]
    for price, touches in lv[:6]:
        if r.low <= price + 0.3 * r.atr and r.close > price and (r.lower_wick_share > 0.4 or r.close > r.open):
            above = [p for p, _ in lv if p > r.close + 0.5 * r.atr]
            pl = _plan(ctx, i, 1, price - 0.5 * r.atr, level=min(above) if above else None)
            if pl:
                return Signal("support_resistance", ctx.tf, 1, _clip(0.3 + 0.1 * touches), *pl,
                              [f"rejection at support {price:.2f} ({touches} touches)"])
        if r.high >= price - 0.3 * r.atr and r.close < price and (r.upper_wick_share > 0.4 or r.close < r.open):
            below = [p for p, _ in lv if p < r.close - 0.5 * r.atr]
            pl = _plan(ctx, i, -1, price + 0.5 * r.atr, level=max(below) if below else None)
            if pl:
                return Signal("support_resistance", ctx.tf, -1, _clip(0.3 + 0.1 * touches), *pl,
                              [f"rejection at resistance {price:.2f} ({touches} touches)"])
    return None


def zones(ctx: Context, i: int, lookback: int = 150) -> list[tuple[int, float, float, int]]:
    """Fresh supply/demand zones at row i: (direction, low, high, impulse row). Demand (+1) is the base candle
    before a strong up candle, supply (-1) before a strong down candle; a zone price has already returned
    into (before row i) is used up."""
    f = ctx.f
    out = []
    lo = max(1, i - lookback)
    body = (f["close"] - f["open"]).to_numpy()
    atr, share = f["atr"].to_numpy(), f["body_share"].to_numpy()
    hi_, lo_ = f["high"].to_numpy(), f["low"].to_numpy()
    for k in range(lo, i - 1):
        if not np.isfinite(atr[k]) or abs(body[k]) < 1.5 * atr[k] or not share[k] >= 0.6:
            continue
        d = 1 if body[k] > 0 else -1
        zl, zh = lo_[k - 1], hi_[k - 1]
        after = slice(k + 1, i)
        touched = (lo_[after] <= zh).any() if d == 1 else (hi_[after] >= zl).any()
        if not touched:
            out.append((d, float(zl), float(zh), k))
    return out


def supply_demand(ctx: Context, i: int, spread: float) -> Signal | None:
    r = ctx.f.iloc[i]
    if not _ok(r.atr):
        return None
    for d, zl, zh, k in reversed(zones(ctx, i)):
        if d == 1 and r.low <= zh and r.close >= zl:
            pl = _plan(ctx, i, 1, zl - 0.3 * r.atr)
            if pl:
                return Signal("supply_demand", ctx.tf, 1, 0.55, *pl, [f"price back in fresh demand zone {zl:.2f}-{zh:.2f}"])
        if d == -1 and r.high >= zl and r.close <= zh:
            pl = _plan(ctx, i, -1, zh + 0.3 * r.atr)
            if pl:
                return Signal("supply_demand", ctx.tf, -1, 0.55, *pl, [f"price back in fresh supply zone {zl:.2f}-{zh:.2f}"])
    return None


def _line(points: list[tuple[int, float]], i: int) -> float | None:
    (x1, y1), (x2, y2) = points[-2], points[-1]
    return None if x2 == x1 else y2 + (y2 - y1) / (x2 - x1) * (i - x2)


def trendline_break(ctx: Context, i: int, spread: float) -> Signal | None:
    if i < 1:
        return None
    c, pc, atr = ctx.f["close"].iat[i], ctx.f["close"].iat[i - 1], ctx.f["atr"].iat[i]
    if not _ok(atr):
        return None
    highs = ctx.swing_highs(i, 120)
    if len(highs) >= 2 and highs[-1][1] < highs[-2][1]:          # falling resistance line
        now, prev = _line(highs, i), _line(highs, i - 1)
        if now is not None and c > now and pc <= prev:
            lows = ctx.swing_lows(i, 60)
            stop = (lows[-1][1] if lows else c - 1.5 * atr) - 0.2 * atr
            pl = _plan(ctx, i, 1, min(stop, c - 0.5 * atr))
            if pl:
                return Signal("trendline_break", ctx.tf, 1, 0.5, *pl, [f"close above falling trendline ({now:.2f})"])
    lows = ctx.swing_lows(i, 120)
    if len(lows) >= 2 and lows[-1][1] > lows[-2][1]:              # rising support line
        now, prev = _line(lows, i), _line(lows, i - 1)
        if now is not None and c < now and pc >= prev:
            highs = ctx.swing_highs(i, 60)
            stop = (highs[-1][1] if highs else c + 1.5 * atr) + 0.2 * atr
            pl = _plan(ctx, i, -1, max(stop, c + 0.5 * atr))
            if pl:
                return Signal("trendline_break", ctx.tf, -1, 0.5, *pl, [f"close below rising trendline ({now:.2f})"])
    return None


def sr_flip(ctx: Context, i: int, spread: float, window: int = 30) -> Signal | None:
    r = ctx.f.iloc[i]
    if not _ok(r.atr) or i < window:
        return None
    closes = ctx.f["close"].to_numpy()[i - window:i]
    for price, touches in levels(ctx, i)[:8]:
        was_below, now_above = (closes[: window // 2] < price).mean() > 0.7, closes[-3:].min() > price
        if was_below and now_above and r.low <= price + 0.3 * r.atr and r.close > price:
            pl = _plan(ctx, i, 1, price - 0.5 * r.atr)
            if pl:
                return Signal("sr_flip", ctx.tf, 1, 0.55, *pl, [f"old resistance {price:.2f} retested as support"])
        was_above, now_below = (closes[: window // 2] > price).mean() > 0.7, closes[-3:].max() < price
        if was_above and now_below and r.high >= price - 0.3 * r.atr and r.close < price:
            pl = _plan(ctx, i, -1, price + 0.5 * r.atr)
            if pl:
                return Signal("sr_flip", ctx.tf, -1, 0.55, *pl, [f"old support {price:.2f} retested as resistance"])
    return None


def range_trading(ctx: Context, i: int, spread: float) -> Signal | None:
    r = ctx.f.iloc[i]
    if not _ok(r.adx, r.bb_pctb, r.rsi, r.bb_mid, r.atr) or r.adx >= 20:
        return None
    if r.bb_pctb <= 0.05 and r.rsi <= 35:
        d, stop = 1, r.low - 0.5 * r.atr
    elif r.bb_pctb >= 0.95 and r.rsi >= 65:
        d, stop = -1, r.high + 0.5 * r.atr
    else:
        return None
    entry = float(r.close)
    risk = (entry - stop) * d
    if risk <= 0 or (r.bb_mid - entry) * d < 0.8 * risk:
        return None
    return Signal("range_trading", ctx.tf, d, _clip((20 - r.adx) / 15 + 0.3), entry, float(stop), float(r.bb_mid),
                  [f"ranging (ADX {r.adx:.0f}), RSI {r.rsi:.0f} at the {'lower' if d == 1 else 'upper'} band"])


def liquidity_sweep(ctx: Context, i: int, spread: float) -> Signal | None:
    sw, r = ctx.sw.iloc[i], ctx.f.iloc[i]
    if not _ok(r.atr):
        return None
    if sw["sweep_low"]:
        pl = _plan(ctx, i, 1, r.low - 0.2 * r.atr)
        if pl:
            return Signal("liquidity_sweep", ctx.tf, 1, 0.5, *pl, ["sell-side liquidity swept, close back above"])
    if sw["sweep_high"]:
        pl = _plan(ctx, i, -1, r.high + 0.2 * r.atr)
        if pl:
            return Signal("liquidity_sweep", ctx.tf, -1, 0.5, *pl, ["buy-side liquidity swept, close back below"])
    return None


def scalping_momentum(ctx: Context, i: int, spread: float) -> Signal | None:
    if ctx.minutes > 5 or i < 3:
        return None
    f = ctx.f
    r = f.iloc[i]
    if not _ok(r.ema9, r.ema21, r.rsi, r.atr, r.hi20, r.lo20):
        return None
    if r.ema9 > r.ema21 and r.rsi > 55 and r.close > r.hi20:
        d, stop = 1, r.close - 1.2 * r.atr
    elif r.ema9 < r.ema21 and r.rsi < 45 and r.close < r.lo20:
        d, stop = -1, r.close + 1.2 * r.atr
    else:
        return None
    if 1.2 * r.atr < 3 * spread:
        return None                                   # the stop would be inside three spreads: costs dominate
    pl = _plan(ctx, i, d, stop, rr=1.5)
    return Signal("scalping_momentum", ctx.tf, d, 0.45, *pl,
                  [f"EMA9/21 {'up' if d == 1 else 'down'}, breakout of the last 20 candles, RSI {r.rsi:.0f}"]) if pl else None


@dataclass(frozen=True)
class Strategy:
    name: str
    label: str
    detect: Callable
    fit: Callable


STRATEGIES = [
    Strategy("trend_following", "Trend following", trend_following, _trend),
    Strategy("market_structure", "Market structure (BOS / CHoCH)", market_structure,
             lambda c, i: 0.5 * _trend(c, i) + 0.5 * _reversal(c, i)),
    Strategy("support_resistance", "Support & resistance", support_resistance,
             lambda c, i: _near_level(c, i) * (0.4 + 0.6 * _range(c, i))),
    Strategy("supply_demand", "Supply & demand", supply_demand,
             lambda c, i: _clip(0.3 * bool(zones(c, i)) + 0.7 * _near_level(c, i) * (1 - 0.5 * _range(c, i)))),
    Strategy("trendline_break", "Trendline break", trendline_break, _breakout),
    Strategy("sr_flip", "Support/resistance flip", sr_flip, lambda c, i: _breakout(c, i) * 0.5 + _near_level(c, i) * 0.5),
    Strategy("range_trading", "Range trading", range_trading, _range),
    Strategy("liquidity_sweep", "Liquidity sweep", liquidity_sweep, _reversal),
    Strategy("scalping_momentum", "Scalping momentum", scalping_momentum,
             lambda c, i: _breakout(c, i) if c.minutes <= 5 else 0.0),
]
BY_NAME = {s.name: s for s in STRATEGIES}
