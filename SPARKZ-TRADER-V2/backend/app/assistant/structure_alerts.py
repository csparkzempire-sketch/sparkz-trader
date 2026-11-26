"""
Structure alerts: a new break of structure (BOS) or change of character (CHoCH) on a watched market.

Checked whenever the MT5 bridge sends a watched market's candles (bridge --watch), on the timeframes in
CHART_ALERT_TFS (default 1h,4h,1d; "off" disables). An alert is raised once, for the candle that just closed,
and only while that candle is recent (history loaded at start never alerts). It goes to the robot's event log as
type STRUCTURE, so it shows on the Event log page and, with Telegram set up, on your phone. Information only:
nothing here trades.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

import pandas as pd

from app.assistant.context import SWING_N, TF_MINUTES
from app.research.structure import structure

DEFAULT_TFS = ("1h", "4h", "1d")
NAMES = {"BOS_UP": "Break of structure UP", "BOS_DOWN": "Break of structure DOWN",
         "CHOCH_UP": "Change of character UP (possible reversal to bullish)",
         "CHOCH_DOWN": "Change of character DOWN (possible reversal to bearish)"}


def alert_timeframes(env=os.environ) -> tuple[str, ...]:
    raw = env.get("CHART_ALERT_TFS", "").strip()
    if raw.lower() in ("off", "none", "0", "false"):
        return ()
    if not raw:
        return DEFAULT_TFS
    return tuple(t.strip() for t in raw.split(",") if t.strip() in TF_MINUTES)


class StructureAlerts:
    def __init__(self, timeframes=None, now=lambda: datetime.now(timezone.utc)):
        self.timeframes = tuple(alert_timeframes() if timeframes is None else timeframes)
        self.now = now
        self.last_checked: dict[tuple[str, str], pd.Timestamp] = {}
        self.recent: list[dict] = []
        self.lock = threading.Lock()

    def check(self, symbol: str, frame_fn, emit) -> list[dict]:
        """`frame_fn(tf)` gives the closed candles; `emit(alert)` delivers one. Returns the new alerts."""
        out = []
        for tf in self.timeframes:
            df = frame_fn(tf)
            if df is None or len(df) < 30:
                continue
            last = pd.Timestamp(df["timestamp"].iloc[-1])
            key = (symbol, tf)
            with self.lock:
                if self.last_checked.get(key) == last:
                    continue
                first_time = key not in self.last_checked
                self.last_checked[key] = last
            closed_at = last + pd.Timedelta(minutes=TF_MINUTES[tf])
            if first_time or self.now() - closed_at.to_pydatetime() > pd.Timedelta(minutes=2 * TF_MINUTES[tf]):
                continue                                  # history, not a fresh close
            ev = structure(df.tail(600).reset_index(drop=True), SWING_N.get(tf, 3))
            e, r = ev["event"].iloc[-1], ev.iloc[-1]
            if not e:
                continue
            level = float(r["sh"] if e.endswith("_UP") else r["sl"])
            a = {"symbol": symbol, "timeframe": tf, "event": e, "level": level, "close": float(df["close"].iloc[-1]),
                 "candle": last.isoformat(), "time": closed_at.isoformat(),
                 "message": f"{symbol} {tf}: {NAMES[e]}, close {float(df['close'].iloc[-1]):.5g} through {level:.5g}"}
            with self.lock:
                self.recent = (self.recent + [a])[-50:]
            out.append(a)
            try:
                emit(a)
            except Exception:
                pass
        return out


ALERTS = StructureAlerts()
