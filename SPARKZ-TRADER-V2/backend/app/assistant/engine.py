"""
The assistant: multi-timeframe, multi-strategy market read, done before the robot may open a trade.

For every timeframe it has (1d, 4h, 1h, 15m, 5m, 1m):
  - trend and structure state;
  - each strategy's FIT (0..1, how well current conditions suit it) and its SIGNAL, if any. Event-type setups
    from the last few candles still count, with a decaying weight, while price has not reached their stop
    or target.
Then:
  - strategy fit %       each strategy's fit, weighted across timeframes, as a share of the total;
  - higher-timeframe bias the trend vote of 1d / 4h / 1h;
  - decision              signals vote, weighted by strength, timeframe, fit and agreement with the bias; the
                          confidence is the size of the majority times the amount of evidence. BUY or SELL
                          only when confidence reaches `min_confidence`, the majority has a signal on the
                          execution timeframe (or one step either side) to time the entry, and the bias does
                          not point the other way; otherwise WAIT, with the reasons;
  - plan                  the strongest agreeing signal near the execution timeframe: entry, stop, target;
  - time and track record the same detector's past signals on the same timeframe (before now only): how many
                          minutes they took to reach target or stop (25th / 50th / 75th percentile) and how
                          often the target came first, net of the spread.

Percentages and time ranges describe the past and the present; they are not promises about the future.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.assistant.context import TF_MINUTES, Context, build
from app.assistant.detectors import BY_NAME, STRATEGIES, Signal

TF_ORDER = ["1d", "4h", "1h", "15m", "5m", "1m"]
TF_WEIGHT = {"1d": 1.0, "4h": 1.0, "1h": 0.9, "15m": 0.8, "5m": 0.6, "1m": 0.5}
BIAS_TFS = ("1d", "4h", "1h")
RECENT = 3                    # candles an event-type setup stays relevant
DECAY = 0.8
STATEFUL = {"trend_following", "range_trading"}       # conditions, re-evaluated every candle
DISCLAIMER = ("Analysis of past and current prices only. Strategy fit, confidence and time ranges are "
              "measurements, not predictions; markets can do anything next.")


def _stats_key(ctx: Context, name: str, spread: float) -> tuple:
    return ctx.tf, name, round(spread, 4)


def resolve(ctx: Context, sig: Signal, k: int, end: int, horizon: int = 120) -> tuple[str, int] | None:
    """Did the signal from row k reach target or stop within rows k+1..end (never beyond `end`)? Returns
    (TARGET|STOP, candles taken); None if neither yet. Both in one candle counts as STOP."""
    hi, lo = ctx.f["high"].to_numpy(), ctx.f["low"].to_numpy()
    d = sig.direction
    for j in range(k + 1, min(end, k + horizon) + 1):
        hit_stop = lo[j] <= sig.stop if d == 1 else hi[j] >= sig.stop
        hit_tgt = hi[j] >= sig.target if d == 1 else lo[j] <= sig.target
        if hit_stop:
            return "STOP", j - k
        if hit_tgt:
            return "TARGET", j - k
    return None


class Assistant:
    def __init__(self, min_confidence: float = 0.55, stats_lookback: int = 1500, stats_horizon: int = 120):
        self.min_confidence = min_confidence
        self.stats_lookback, self.stats_horizon = stats_lookback, stats_horizon
        self._stats: dict[tuple, tuple[pd.Timestamp, dict]] = {}

    # ------------------------------------------------------------------------------------------------ history
    def history(self, ctx: Context, name: str, end: int, spread: float) -> dict:
        """Past signals of one strategy on one timeframe, before row `end`: samples, hit rate, average R net
        of the spread, minutes to resolution. Cached per candle."""
        key = _stats_key(ctx, name, spread)
        stamp = ctx.f["timestamp"].iat[end]
        if key in self._stats:                           # reuse for up to 20 candles: it is a long-run average
            seen, cached = self._stats[key]
            if pd.Timedelta(0) <= stamp - seen < pd.Timedelta(minutes=20 * ctx.minutes):
                return cached
        det = BY_NAME[name].detect
        outcomes, minutes, prev = [], [], None
        for k in range(max(220, end - self.stats_lookback), end):
            sig = det(ctx, k, spread)
            cur = sig.direction if sig else None
            if sig is None or cur == prev:               # count a setup once, when it appears
                prev = cur
                continue
            prev = cur
            res = resolve(ctx, sig, k, end, self.stats_horizon)
            if res is None:
                continue
            cost = spread / sig.risk if sig.risk > 0 else 0.0
            outcomes.append((sig.rr if res[0] == "TARGET" else -1.0) - cost)
            minutes.append(res[1] * ctx.minutes)
        out = {"samples": len(outcomes)}
        if outcomes:
            o, m = np.array(outcomes), np.array(minutes)
            out.update({"hit_rate_pct": round(100 * float((o > 0).mean()), 1), "avg_r_net": round(float(o.mean()), 3),
                        "minutes": {"fast_p25": int(np.percentile(m, 25)), "typical_p50": int(np.percentile(m, 50)),
                                    "slow_p75": int(np.percentile(m, 75))}})
        self._stats[key] = (stamp, out)
        return out

    # ----------------------------------------------------------------------------------------------- analysis
    def analyze(self, frames: dict[str, pd.DataFrame], spread: float, execution_tf: str = "15m",
                with_history: bool = True) -> dict:
        """Live use: closed candles per timeframe -> report at the latest candle of each."""
        ctxs = {tf: build(df, tf) for tf, df in frames.items() if tf in TF_MINUTES and len(df) >= 60}
        return self.analyze_at(ctxs, {tf: len(c) - 1 for tf, c in ctxs.items()}, spread, execution_tf, with_history)

    def _signals(self, ctx: Context, i: int, spread: float) -> list[tuple[Signal, int]]:
        """Active signals at row i: (signal, age in candles)."""
        out, price = [], ctx.f["close"].iat[i]
        for s in STRATEGIES:
            for age in range(0, 1 if s.name in STATEFUL else RECENT):
                if i - age < 1:
                    break
                sig = s.detect(ctx, i - age, spread)
                if sig is None:
                    continue
                if age and resolve(ctx, sig, i - age, i, RECENT) is not None:
                    break                                 # already hit its target or stop
                if age:
                    sig.entry = float(price)
                    if (sig.entry - sig.stop) * sig.direction <= 0 or (sig.target - sig.entry) * sig.direction <= 0:
                        break
                out.append((sig, age))
                break
        return out

    def analyze_at(self, ctxs: dict[str, Context], idx: dict[str, int], spread: float, execution_tf: str = "15m",
                   with_history: bool = True) -> dict:
        tfs = [tf for tf in TF_ORDER if tf in ctxs and idx.get(tf, -1) >= 0]
        per_tf, all_sigs = {}, []
        fit_total = {s.name: 0.0 for s in STRATEGIES}
        weight_total = 0.0
        bias_score = bias_weight = 0.0
        for tf in tfs:
            c, i = ctxs[tf], idx[tf]
            r = c.f.iloc[i]
            ema_dir = 0 if not np.isfinite(r.ema_slow) else (1 if r.ema_fast > r.ema_slow else -1)
            st_dir = int(c.st["trend"].iat[i])
            trend = ema_dir + st_dir
            fits = {s.name: round(float(s.fit(c, i)), 3) for s in STRATEGIES}
            w = TF_WEIGHT[tf]
            for k, v in fits.items():
                fit_total[k] += w * v
            weight_total += w
            if tf in BIAS_TFS:
                bias_score += w * trend / 2
                bias_weight += w
            sigs = self._signals(c, i, spread)
            for sig, age in sigs:
                all_sigs.append((sig, age, fits[sig.strategy]))
            per_tf[tf] = {"time": str(r.timestamp), "close": round(float(r.close), 3),
                          "trend": "UP" if trend > 0 else "DOWN" if trend < 0 else "MIXED",
                          "adx": None if not np.isfinite(r.adx) else round(float(r.adx), 1),
                          "rsi": None if not np.isfinite(r.rsi) else round(float(r.rsi), 1),
                          "last_structure_event": next((e for e in c.st["event"].iloc[max(0, i - 20):i + 1][::-1] if e), None),
                          "strategy_fit": fits,
                          "signals": [{**s.to_dict(), "age_candles": a} for s, a in sigs]}
        bias = bias_score / bias_weight if bias_weight else 0.0
        bias_dir = 1 if bias > 0.25 else -1 if bias < -0.25 else 0
        fit_sum = sum(fit_total.values())
        fit_pct = {k: round(100 * v / fit_sum, 1) if fit_sum else 0.0 for k, v in fit_total.items()}

        votes = {1: 0.0, -1: 0.0}
        weighted = []
        for sig, age, fit in all_sigs:
            w = sig.strength * TF_WEIGHT[sig.tf] * (0.5 + fit) * DECAY ** age
            if bias_dir and sig.direction != bias_dir:
                w *= 0.5
            votes[sig.direction] += w
            weighted.append((w, sig, age))
        total = votes[1] + votes[-1]
        net = (votes[1] - votes[-1]) / total if total else 0.0
        confidence = abs(net) * min(1.0, total / 1.5)
        direction = 1 if net > 0 else -1 if net < 0 else 0
        ex_min = TF_MINUTES.get(execution_tf, 15)
        near = [(w, s, a) for w, s, a in weighted if s.direction == direction
                and ex_min / 4 <= TF_MINUTES[s.tf] <= ex_min * 4]
        reasons = []
        action = "WAIT"
        if not all_sigs:
            reasons.append("no strategy has a setup on any timeframe")
        elif confidence < self.min_confidence:
            reasons.append(f"confidence {100 * confidence:.0f}% below {100 * self.min_confidence:.0f}%"
                           f" (buy votes {votes[1]:.2f}, sell votes {votes[-1]:.2f})")
        elif not near:
            reasons.append(f"no {('buy' if direction == 1 else 'sell')} setup near the {execution_tf} timeframe to time an entry")
        elif bias_dir and direction != bias_dir:
            reasons.append("higher timeframes point the other way")
        else:
            action = "BUY" if direction == 1 else "SELL"
        plan = None
        if near:
            w, best, age = max(near, key=lambda x: x[0])
            plan = {**best.to_dict(), "age_candles": age, "weight": round(w, 3)}
            if with_history and best.tf in ctxs:
                hist = self.history(ctxs[best.tf], best.strategy, idx[best.tf], spread)
                plan["history"] = hist
                if hist.get("samples"):
                    reasons.append(f"{best.strategy} on {best.tf}: target first in {hist['hit_rate_pct']}% of "
                                   f"{hist['samples']} past setups, typically {hist['minutes']['typical_p50']} min")
        agree = sorted({s.strategy for _, s, _ in weighted if s.direction == direction})
        if action != "WAIT":
            reasons.insert(0, f"{len(agree)} strateg{'y' if len(agree) == 1 else 'ies'} agree: {', '.join(agree)}")
        last_tf = tfs[-1] if tfs else None
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "price": per_tf[last_tf]["close"] if last_tf else None,
            "action": action, "confidence_pct": round(100 * confidence, 1),
            "direction_votes": {"buy": round(votes[1], 3), "sell": round(votes[-1], 3)},
            "higher_timeframe_bias": "BULLISH" if bias_dir == 1 else "BEARISH" if bias_dir == -1 else "MIXED",
            "execution_timeframe": execution_tf,
            "strategy_fit_pct": dict(sorted(fit_pct.items(), key=lambda kv: -kv[1])),
            "plan": plan, "reasons": reasons, "timeframes": per_tf,
            "min_confidence_pct": round(100 * self.min_confidence, 1), "spread": spread,
            "disclaimer": DISCLAIMER,
        }
