"""
Lot-escalation robots on real gold candles: the "trend pyramid" seen in social-media videos, and the "flip and
increase" (zone-recovery / martingale) robot, simulated on a real account with margin and stop-out.

  python -m app.research.lot_escalation [--spread 0.24] [--slippage 0.05] [--leverage 2000]

Both robots take their direction from the same trend filter: the last CLOSED 4h candle's EMA20 above its EMA50
and the last closed 1h close above its EMA50 = up (mirror = down, anything else = no trade). A new cycle may
start only on a 15-minute boundary, in the trend's direction.

TREND PYRAMID (what the video shows: BUY 0.02, then 0.35 at +5.3, then 0.55 at +14.8, target about +40):
  first trade, then bigger trades each time price has moved `add_at` dollars in favour, all closed together when
  price reaches `target` dollars beyond the first entry. With `stop`, the whole stack is closed if price falls
  `stop` dollars below the first entry; after each add the stop moves up to the previous add's price.

FLIP AND INCREASE (zone recovery without hedging):
  one trade; if price moves `flip` dollars against it, it is closed and the opposite side is opened with
  `mult` times the lots; the cycle ends when its total result (closed legs + open leg) reaches `target_usd` per
  0.01 base lot, or with a loss when a flip is due after `max_flips` flips.

Account: XAUUSD 100 oz per lot; buys fill at the ask, sells at the bid (mid +/- half the spread), plus slippage
against the robot on every fill. Margin = lots x 100 x price / leverage. A new trade (or add) is refused when the
free margin would go negative. STOP-OUT: when equity falls to 0 the broker closes everything (negative balance
protection: balance floors at 0). The account is "blown" when it is stopped out or can no longer afford 0.01 lot.

Base lot (compounding, as these robots do): `lot_per_100` lots per 100 USD of balance at the start of each cycle,
rounded down to 0.01, at least 0.01.

Within a 1-minute candle the ADVERSE extreme is processed before the favourable one (stop-out, stops and flips
before adds and targets), the conservative order: the robot is never credited with a target it might have
reached only after its stop.

Two tests per robot and starting balance:
  full     one run over all the 1m history (Jan to Sep 2025 by default);
  rolling  a fresh account started every 7 days, run for 30 days: how often it is blown within a month, and the
           typical 30-day result (median) next to the best and worst.

Writes reports/studies/lot_escalation.json. Simulation only.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from app.config import REPORTS_DIR
from app.market.history import load_history

CONTRACT = 100.0


@dataclass
class Pyramid:
    name: str
    lot_per_100: float              # first trade's lots per 100 USD of balance
    adds: list                      # [(dollars in favour from the first entry, lot multiple of the first trade)]
    target: float                   # dollars from the first entry: close everything
    stop: float | None = None       # dollars against the first entry (None: no stop, the video)
    trail: bool = False             # after each add, stop moves to the previous add's price
    kind: str = "pyramid"


@dataclass
class Flip:
    name: str
    lot_per_100: float
    mult: float
    flip: float                     # dollars against the open leg: flip
    target_usd: float               # cycle profit per 0.01 base lot
    max_flips: int
    kind: str = "flip"


@dataclass
class Compound:
    """Compounding scalps toward a target (the user's reading of the video): read the trend, set a target and a
    stop, then keep `slots` identical positions open in that direction; each takes profit after `step` dollars
    and is reopened at once with lots recomputed from the new balance; the campaign ends when price reaches the
    target (everything closed in profit) or the stop (everything closed at a loss). `step=None`: no scalping, the
    positions are simply held to the target or the stop (the control).
    Sizing: "risk" = total lots chosen so that the stop loses `risk_pct` of the balance; "margin" = total lots
    using `margin_use` of what the margin allows (the video's margin level of 116-300 %)."""
    name: str
    target: float
    stop: float
    step: float | None
    slots: int = 7
    sizing: str = "risk"
    risk_pct: float = 2.0
    margin_use: float = 0.5
    kind: str = "compound"


VARIANTS = [
    Pyramid("pyramid_video", lot_per_100=0.40, adds=[(5.3, 17.5), (14.8, 27.5)], target=40.0),
    Pyramid("pyramid_video_with_stops", lot_per_100=0.40, adds=[(5.3, 17.5), (14.8, 27.5)], target=40.0,
            stop=5.0, trail=True),
    Pyramid("pyramid_moderate", lot_per_100=0.01, adds=[(5.0, 1.0), (10.0, 1.0), (15.0, 1.0)], target=40.0,
            stop=10.0, trail=True),
    Flip("flip_double", lot_per_100=0.01, mult=2.0, flip=3.0, target_usd=1.0, max_flips=8),
    Flip("flip_gentle", lot_per_100=0.01, mult=1.5, flip=5.0, target_usd=2.0, max_flips=6),
    Flip("flip_double_12", lot_per_100=0.01, mult=2.0, flip=3.0, target_usd=1.0, max_flips=12),
    # the same ideas with 10x smaller lots (0.01 lot per 1,000 USD): does the idea itself make money when the
    # account is not at risk of being wiped out?
    Pyramid("pyramid_small_lots", lot_per_100=0.001, adds=[(5.0, 1.0), (10.0, 1.0), (15.0, 1.0)], target=40.0,
            stop=10.0, trail=True),
    Flip("flip_double_small_lots", lot_per_100=0.001, mult=2.0, flip=3.0, target_usd=1.0, max_flips=8),
    # flips $3 apart are small next to 2025 gold's moves; a wider zone with a bigger target, still small lots
    Flip("flip_wide_small_lots", lot_per_100=0.001, mult=2.0, flip=10.0, target_usd=3.0, max_flips=6),
    # compounding scalps toward a target: the user's description of the video
    Compound("compound_scalps_risk2", target=25.0, stop=10.0, step=1.0, sizing="risk", risk_pct=2.0),
    Compound("compound_scalps_risk10", target=25.0, stop=10.0, step=1.0, sizing="risk", risk_pct=10.0),
    Compound("compound_scalps_video_margin", target=25.0, stop=10.0, step=1.0, sizing="margin", margin_use=0.5),
    Compound("hold_to_target_risk2", target=25.0, stop=10.0, step=None, sizing="risk", risk_pct=2.0),
]


# ---------------------------------------------------------------------------------------------- data
def trend(m1: pd.DataFrame) -> np.ndarray:
    """+1 / -1 / 0 per 1m candle, from 4h and 1h candles CLOSED before that candle opens."""
    s = m1.set_index("timestamp")["close"]
    out = pd.Series(0.0, index=m1["timestamp"])
    frames = {}
    for rule, mins in (("1h", 60), ("4h", 240)):
        c = s.resample(rule, label="left", closed="left").last().dropna()
        df = pd.DataFrame({"close": c, "e20": c.ewm(span=20, adjust=False).mean(),
                           "e50": c.ewm(span=50, adjust=False).mean()})
        df.index = df.index + pd.Timedelta(minutes=mins)                  # known from the candle's close
        frames[rule] = df.reindex(out.index, method="ffill")
    h1, h4 = frames["1h"], frames["4h"]
    up = (h4["e20"] > h4["e50"]) & (h1["close"] > h1["e50"])
    dn = (h4["e20"] < h4["e50"]) & (h1["close"] < h1["e50"])
    out[up.to_numpy()] = 1.0
    out[dn.to_numpy()] = -1.0
    return out.to_numpy()


# ---------------------------------------------------------------------------------------------- engine
@dataclass
class Account:
    balance: float
    spread: float
    slip: float
    leverage: float
    pos: list = field(default_factory=list)          # [direction, lots, entry]
    max_lots: float = 0.0
    blown_at: str | None = None

    def fill(self, d: int, mid: float) -> float:
        """Price a trade in direction d executes at."""
        return mid + d * (self.spread / 2 + self.slip)

    def open_pnl(self, mid: float) -> float:
        return sum(d * ((mid - d * self.spread / 2) - e) * lots * CONTRACT for d, lots, e in self.pos)

    def margin(self, mid: float, extra: float = 0.0) -> float:
        return (sum(l for _, l, _ in self.pos) + extra) * CONTRACT * mid / self.leverage

    def can_open(self, lots: float, mid: float) -> bool:
        return self.balance + self.open_pnl(mid) - self.margin(mid, lots) >= 0

    def open(self, d: int, lots: float, mid: float) -> None:
        self.pos.append([d, lots, self.fill(d, mid)])
        self.max_lots = max(self.max_lots, sum(l for _, l, _ in self.pos))

    def close_all(self, mid: float) -> float:
        pnl = sum(dd * (self.fill(-dd, mid) - e) * lots * CONTRACT for dd, lots, e in self.pos)
        self.balance = max(0.0, self.balance + pnl)
        self.pos = []
        return pnl

    def stopped_out(self, mid: float) -> bool:
        return bool(self.pos) and self.balance + self.open_pnl(mid) <= 0


def base_lot(balance: float, per_100: float) -> float:
    return max(0.01, math.floor(balance / 100 * per_100 * 100 + 1e-9) / 100)


def compound_lots(v, acc: "Account", mid: float, entry_to_stop: float) -> float:
    """Lots per slot for a Compound campaign, from the current balance."""
    if v.sizing == "risk":
        per_lot_loss = (entry_to_stop + acc.spread + 2 * acc.slip) * CONTRACT      # USD lost per lot at the stop
        total = acc.balance * v.risk_pct / 100 / max(per_lot_loss, 1e-9)
    else:
        total = v.margin_use * acc.balance * acc.leverage / (CONTRACT * mid)
    return max(0.01, math.floor(total / v.slots * 100 + 1e-9) / 100)


def run_compound(v, t, o, h, lo, c, tr, start_balance, spread, slip, leverage, i0=0, i1=None) -> dict:
    i1 = len(c) if i1 is None else i1
    acc = Account(start_balance, spread, slip, leverage)
    campaigns: list[float] = []
    hits = stops = 0
    scalps = 0
    eq_peak, max_dd = start_balance, 0.0
    camp = None
    for i in range(i0, i1):
        mid_o, hi, low, close = o[i], h[i], lo[i], c[i]
        if camp is None and t[i].minute % 15 == 0 and tr[i] != 0:
            d = int(tr[i])
            lot = compound_lots(v, acc, mid_o, v.stop)
            if acc.balance <= 0 or not acc.can_open(lot, mid_o):
                acc.blown_at = str(t[i])
                break
            camp = {"d": d, "tgt": mid_o + d * v.target, "stop": mid_o - d * v.stop, "start": acc.balance}
            for _ in range(v.slots):
                if acc.can_open(lot, mid_o):
                    acc.open(d, lot, mid_o)
        if camp is None:
            continue
        d = camp["d"]
        adverse, favour = (low, hi) if d == 1 else (hi, low)
        if acc.stopped_out(adverse) and (adverse - camp["stop"]) * d > 0 or (
                acc.stopped_out(camp["stop"]) and (adverse - camp["stop"]) * d <= 0):
            acc.close_all(adverse)
            acc.balance = 0.0
            campaigns.append(-camp["start"])
            acc.blown_at = str(t[i])
            break
        if (adverse - camp["stop"]) * d <= 0:                       # the stop: everything closed
            acc.close_all(camp["stop"])
            campaigns.append(acc.balance - camp["start"])
            stops += 1
            camp = None
            continue
        if (favour - camp["tgt"]) * d >= 0:                         # the target: everything closed
            # scalps on the way up first (they were reached before the target)
            if v.step is not None:
                scalps += sum(int(abs(camp["tgt"] - p[2]) // v.step) for p in acc.pos)
            acc.close_all(camp["tgt"])
            campaigns.append(acc.balance - camp["start"])
            hits += 1
            camp = None
            continue
        if v.step is not None:                                      # scalp take-profits, reopened at once
            changed = True
            while changed:
                changed = False
                for p in list(acc.pos):
                    pd_, lots, entry = p
                    tp_mid = entry + pd_ * v.step + pd_ * (spread / 2 + slip)
                    if (favour - tp_mid) * pd_ >= 0 and (camp["tgt"] - tp_mid) * pd_ > 0:
                        acc.balance += v.step * lots * CONTRACT
                        acc.pos.remove(p)
                        scalps += 1
                        new = compound_lots(v, acc, tp_mid, abs(tp_mid - camp["stop"]))
                        if acc.can_open(new, tp_mid):
                            acc.open(pd_, new, tp_mid)
                        changed = True
        eq = acc.balance + acc.open_pnl(close)
        eq_peak = max(eq_peak, eq)
        if eq_peak > 0:
            max_dd = max(max_dd, (eq_peak - eq) / eq_peak)
    else:
        if acc.pos:
            acc.close_all(c[i1 - 1])
    cy = np.asarray(campaigns)
    n = hits + stops
    cost = spread + 2 * slip
    return {"start": str(t[i0]), "end": str(t[min(i1, len(t)) - 1]), "start_balance": start_balance,
            "final_balance": round(acc.balance, 2), "blown": acc.blown_at is not None, "blown_at": acc.blown_at,
            "return_pct": round(100 * (acc.balance / start_balance - 1), 1), "cycles": int(len(cy)),
            "cycle_win_pct": round(100 * float((cy > 0).mean()), 1) if len(cy) else None,
            "avg_win_usd": round(float(cy[cy > 0].mean()), 2) if (cy > 0).any() else None,
            "worst_cycle_usd": round(float(cy.min()), 2) if len(cy) else None,
            "max_lots_open": round(acc.max_lots, 2), "max_drawdown_pct": round(100 * max_dd, 1),
            "target_reached_pct": round(100 * hits / n, 1) if n else None,
            "break_even_target_pct": round(100 * (v.stop + cost) / (v.target + v.stop), 1), "scalps": scalps}


def run(v, t, o, h, lo, c, tr, start_balance: float, spread: float, slip: float, leverage: float,
        i0: int = 0, i1: int | None = None) -> dict:
    if v.kind == "compound":
        return run_compound(v, t, o, h, lo, c, tr, start_balance, spread, slip, leverage, i0, i1)
    i1 = len(c) if i1 is None else i1
    acc = Account(start_balance, spread, slip, leverage)
    cycles: list[float] = []
    eq_peak, max_dd = start_balance, 0.0
    st: dict = {}                                     # the running cycle

    def end_cycle(mid):
        nonlocal st
        pnl = acc.close_all(mid) + st.get("realized", 0.0)
        cycles.append(pnl)
        st = {}

    for i in range(i0, i1):
        mid_o, hi, low, close = o[i], h[i], lo[i], c[i]
        # ---- start a cycle (15-minute boundary, trend known, flat)
        if not acc.pos and t[i].minute % 15 == 0 and tr[i] != 0:
            lot = base_lot(acc.balance, v.lot_per_100)
            if not acc.can_open(lot, mid_o):
                lot = 0.01                                # what the margin allows
            if acc.balance <= 0 or not acc.can_open(lot, mid_o):
                acc.blown_at = str(t[i])
                break
            d = int(tr[i])
            acc.open(d, lot, mid_o)
            st = {"start_balance": acc.balance, "d": d, "lot0": lot, "first": mid_o, "realized": 0.0, "adds": 0, "flips": 0,
                  "stop": (mid_o - d * v.stop) if (v.kind == "pyramid" and v.stop) else None,
                  "prev": mid_o}
        if not acc.pos:
            continue
        d = st["d"]
        adverse, favour = (low, hi) if d == 1 else (hi, low)
        # ---- adverse side first
        if acc.stopped_out(adverse):
            # the broker closes at the price where equity reaches 0 (no worse: negative balance protection)
            acc.close_all(adverse)
            cycles.append(-st["start_balance"])
            acc.blown_at = str(t[i])
            break
        if v.kind == "pyramid":
            if st["stop"] is not None and (adverse - st["stop"]) * d <= 0:
                end_cycle(st["stop"])
                continue
        else:
            leg_d, leg_lots, leg_entry = acc.pos[0]
            level = leg_entry - leg_d * v.flip
            flipped = False
            while acc.pos and (adverse - level) * leg_d <= 0:
                if acc.stopped_out(level):                   # equity reached 0 before the flip level
                    break
                st["realized"] += acc.close_all(level)
                if acc.balance <= 0:
                    cycles.append(-st["start_balance"])
                    st = {}
                    acc.blown_at = str(t[i])
                    break
                if st["flips"] >= v.max_flips:
                    cycles.append(st["realized"])
                    st = {}
                    break
                new_lots = round(leg_lots * v.mult, 2)
                if not acc.can_open(new_lots, level):
                    cycles.append(st["realized"])
                    st = {}
                    break
                st["flips"] += 1
                flipped = True
                leg_d = -leg_d
                st["d"] = leg_d
                acc.open(leg_d, new_lots, level)
                leg_lots, leg_entry = new_lots, acc.pos[0][2]
                level = leg_entry - leg_d * v.flip
                # the new leg's adverse extreme within this candle is the other side of the range
                adverse = low if leg_d == 1 else hi
            if acc.blown_at:
                break
            if acc.pos and acc.stopped_out(adverse):
                cycles.append(-st["start_balance"])
                acc.close_all(adverse)
                acc.blown_at = str(t[i])
                break
            if not acc.pos or flipped:
                continue            # after a flip inside this candle its favourable extreme may have come first
            d = st["d"]
            favour = hi if d == 1 else low
        # ---- favourable side
        if v.kind == "pyramid":
            while st["adds"] < len(v.adds):
                dist, m = v.adds[st["adds"]]
                lvl = st["first"] + d * dist
                if (favour - lvl) * d < 0:
                    break
                lots = round(st["lot0"] * m, 2)
                st["adds"] += 1
                if acc.can_open(lots, lvl):
                    acc.open(d, lots, lvl)
                    if v.trail and v.stop is not None:
                        st["stop"] = st["prev"]
                    st["prev"] = lvl
            tgt = st["first"] + d * v.target
            if (favour - tgt) * d >= 0:
                end_cycle(tgt)
                continue
        else:
            leg_d, leg_lots, leg_entry = acc.pos[0]
            need = v.target_usd * st["lot0"] / 0.01 - st["realized"]
            # mid at which the open leg's exit price gives `need`: exit = entry + d * need / (lots * 100)
            exit_px = leg_entry + leg_d * need / (leg_lots * CONTRACT)
            tgt_mid = exit_px + leg_d * (spread / 2 + slip)
            if (favour - tgt_mid) * leg_d >= 0:
                end_cycle(tgt_mid)
                continue
        eq = acc.balance + acc.open_pnl(close)
        eq_peak = max(eq_peak, eq)
        if eq_peak > 0:
            max_dd = max(max_dd, (eq_peak - eq) / eq_peak)
    else:
        if acc.pos:
            end_cycle(c[i1 - 1])
    cy = np.asarray(cycles)
    return {"start": str(t[i0]), "end": str(t[min(i1, len(t)) - 1]), "start_balance": start_balance,
            "final_balance": round(acc.balance, 2), "blown": acc.blown_at is not None, "blown_at": acc.blown_at,
            "return_pct": round(100 * (acc.balance / start_balance - 1), 1), "cycles": int(len(cy)),
            "cycle_win_pct": round(100 * float((cy > 0).mean()), 1) if len(cy) else None,
            "avg_win_usd": round(float(cy[cy > 0].mean()), 2) if (cy > 0).any() else None,
            "worst_cycle_usd": round(float(cy.min()), 2) if len(cy) else None,
            "max_lots_open": round(acc.max_lots, 2), "max_drawdown_pct": round(100 * max_dd, 1)}


def rolling(v, arrays, t, start_balance, spread, slip, leverage, days=30, every=7) -> dict:
    ts = pd.DatetimeIndex(t)
    starts = pd.date_range(ts[0].normalize() + pd.Timedelta(days=1), ts[-1] - pd.Timedelta(days=days),
                           freq=f"{every}D")
    runs = []
    for s in starts:
        i0 = int(ts.searchsorted(s))
        i1 = int(ts.searchsorted(s + pd.Timedelta(days=days)))
        runs.append(run(v, t, *arrays, start_balance, spread, slip, leverage, i0, i1))
    r = np.array([x["return_pct"] for x in runs])
    blown = [x for x in runs if x["blown"]]
    return {"runs": len(runs), "blown_within_30_days_pct": round(100 * len(blown) / len(runs), 1) if runs else None,
            "median_30d_return_pct": round(float(np.median(r)), 1), "best_30d_return_pct": round(float(r.max()), 1),
            "worst_30d_return_pct": round(float(r.min()), 1),
            "profitable_months_pct": round(100 * float((r > 0).mean()), 1)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spread", type=float, default=0.24)
    ap.add_argument("--slippage", type=float, default=0.05)
    ap.add_argument("--leverage", type=float, default=2000.0)    # Exness offers up to 1:2000 on small accounts
    ap.add_argument("--balances", default="5,100,1000,10000")
    ap.add_argument("--source", default="dukascopy")
    a = ap.parse_args(argv)
    t0 = time.time()
    m1 = load_history("XAUUSD", "1m", a.source).sort_values("timestamp").reset_index(drop=True)
    tr = trend(m1)
    t = list(pd.DatetimeIndex(m1["timestamp"]))
    arrays = (m1["open"].to_numpy(), m1["high"].to_numpy(), m1["low"].to_numpy(), m1["close"].to_numpy(), tr)
    balances = [float(x) for x in a.balances.split(",")]
    out = {"meta": {"spread": a.spread, "slippage": a.slippage, "leverage": a.leverage,
                    "data": f"{t[0]} .. {t[-1]}", "candles": len(t), "notes": __doc__.split("Account:")[1][:900]},
           "variants": {}}
    for v in VARIANTS:
        res = {"settings": asdict(v)}
        for b in balances:
            res[f"${b:g}"] = {"full": run(v, t, *arrays, b, a.spread, a.slippage, a.leverage),
                              "rolling_30d": rolling(v, arrays, t, b, a.spread, a.slippage, a.leverage)}
            f, rl = res[f"${b:g}"]["full"], res[f"${b:g}"]["rolling_30d"]
            print(f"{v.name:26s} ${b:>6g}: full {f['final_balance']:>10} ({'BLOWN ' + f['blown_at'][:10] if f['blown'] else 'alive'})"
                  f" cycles {f['cycles']:>4} win {f['cycle_win_pct']}% worst {f['worst_cycle_usd']} maxlots {f['max_lots_open']}"
                  f" | 30d: blown {rl['blown_within_30_days_pct']}% median {rl['median_30d_return_pct']}% best "
                  f"{rl['best_30d_return_pct']}% worst {rl['worst_30d_return_pct']}%", flush=True)
        out["variants"][v.name] = res
    (REPORTS_DIR / "studies").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "studies" / "lot_escalation.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"done ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
