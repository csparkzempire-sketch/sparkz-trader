"""
Live use of the assistant: gather closed candles for every timeframe the data source can give, run the
analysis, and keep the latest report.

Timeframes come from the provider when it serves them (the MT5 bridge serves 1m..1d); otherwise they are
built from the strategy timeframe's candles (higher timeframes only: a 15m feed cannot make 1m candles).
"""

from __future__ import annotations

import time

import pandas as pd

from app.assistant.context import TF_MINUTES, resample
from app.assistant.engine import TF_ORDER, Assistant

FRAME_BARS = {"1m": 3000, "5m": 2000, "15m": 1500, "1h": 1500, "4h": 1000, "1d": 600}


def gather_frames(provider, base_tf: str, base: pd.DataFrame | None = None,
                  until: pd.Timestamp | None = None) -> dict[str, pd.DataFrame]:
    """Closed candles per timeframe. `until`: drop candles that close after this time (a paper runner catching
    up on several closed bars analyses each as of its own close)."""
    frames: dict[str, pd.DataFrame] = {}
    for tf in TF_ORDER:
        try:
            df = provider.get_candles(tf, FRAME_BARS[tf])
        except Exception:
            continue
        if df is not None and len(df):
            frames[tf] = df
    if base is not None and len(base) and base_tf not in frames:
        frames[base_tf] = base
    if base_tf in frames:                                       # higher timeframes built from the base
        src = frames[base_tf]
        for tf in TF_ORDER:
            if tf not in frames and TF_MINUTES[tf] > TF_MINUTES.get(base_tf, 10 ** 9):
                r = resample(src, tf)
                last_close = pd.to_datetime(src["timestamp"], utc=True).iloc[-1] + pd.Timedelta(minutes=TF_MINUTES[base_tf])
                frames[tf] = r[r["timestamp"] + pd.Timedelta(minutes=TF_MINUTES[tf]) <= last_close]
    if until is not None:
        for tf, df in list(frames.items()):
            ts = pd.to_datetime(df["timestamp"], utc=True)
            frames[tf] = df[ts + pd.Timedelta(minutes=TF_MINUTES[tf]) <= until].reset_index(drop=True)
    return {tf: df for tf, df in frames.items() if len(df)}


class LiveAssistant:
    def __init__(self, min_confidence: float, refresh_seconds: float = 30.0, wall=time.time):
        self.engine = Assistant(min_confidence)
        self.refresh_seconds, self.wall = refresh_seconds, wall
        self.report: dict | None = None
        self.updated: float | None = None
        self.error: str | None = None

    def run(self, provider, base_tf: str, spread: float, base: pd.DataFrame | None = None,
            until: pd.Timestamp | None = None) -> dict | None:
        try:
            frames = gather_frames(provider, base_tf, base, until)
            if not frames:
                self.error = "no candles yet"
                return None
            self.report = self.engine.analyze(frames, spread, base_tf)
            self.report["timeframes_available"] = [tf for tf in TF_ORDER if tf in self.report["timeframes"]]
            self.updated, self.error = self.wall(), None
        except Exception as e:                                   # never stops the paper loop
            self.error = f"{type(e).__name__}: {e}"
        return self.report

    def stale(self) -> bool:
        return self.updated is None or self.wall() - self.updated >= self.refresh_seconds
