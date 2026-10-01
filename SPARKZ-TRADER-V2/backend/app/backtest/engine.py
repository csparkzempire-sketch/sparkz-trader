"""
Backtest engine: replays closed candles through the SAME Robot (strategy, risk
manager, simulated executor, paper account) that paper trading uses.

Only the source of prices differs. A HistoricalDataProvider hands out candles
one at a time (it refuses to show a candle beyond the cursor), and each candle
is turned into a short synthetic tick path inside the bar:

  open (gap)  ->  first extreme  ->  second extreme  ->  close  ->  bar close

Which extreme comes first is unknowable from OHLC, and for a grid it matters a
lot. Visiting the extreme AGAINST an open basket first looks "pessimistic" but
is the opposite: the grid adds at the dip and takes its target on the rebound
inside the same candle, a round trip real prices may never have made. On a
driftless random walk (8 seeds, default settings) that ordering turned an
expected loss of about the trading costs into a profit (+249 USD average vs
-387 USD with the favourable extreme first).

So `execution.intrabar_order` (while a basket is open or about to open):
  FAVOURABLE_FIRST  (default) extreme in the basket's favour first, then the adverse one
  ADVERSE_FIRST     the flattering order, kept for sensitivity checks
  RANDOM            50/50 per candle, seeded
With no basket the usual heuristic applies (up candle: open-low-high-close;
down candle: open-high-low-close). Reports can re-run with the other order to
show how much of a result depends on this assumption.

Features (ATR, EMAs, RSI, regime) are computed once over the whole series with
indicators that only look backwards; the state the strategy sees at bar i comes
from row i. tests/test_no_lookahead.py checks that truncating the series does
not change any earlier row.

Assumptions shown on every report: synthetic intrabar path, fills at trigger
levels on continuous moves and at the tick price after gaps, the configured
spread / slippage / commission / delay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from app.config import TIMEFRAMES, Settings
from app.engine.event_log import EventLog
from app.engine.robot import Robot
from app.market.history import validate
from app.market.instruments import get_instrument
from app.market.market_engine import featurize
from app.market.providers.base import Tick
from app.market.providers.historical_provider import HistoricalDataProvider

ASSUMPTIONS = [
    "Prices inside each candle follow a synthetic path open -> extreme -> extreme -> close; the real order is unknown.",
    "While a basket is open the candle's extreme in the basket's favour is visited first (intrabar_order); "
    "the opposite order flatters grids.",
    "Levels crossed by a continuous move fill at the level; levels jumped over by a gap fill at the next price.",
    "Spread, slippage, commission and execution delay as configured; no partial fills, no requotes.",
    "Swap / overnight financing is not modelled.",
    "Results describe this period under these assumptions only; they are not a forecast.",
]


@dataclass
class BacktestResult:
    settings: Settings
    label: str
    robot: Robot
    candles: pd.DataFrame
    features: pd.DataFrame
    started: datetime | None
    ended: datetime | None
    assumptions: list[str] = field(default_factory=lambda: list(ASSUMPTIONS))

    @property
    def baskets(self):
        return self.robot.completed

    @property
    def log(self) -> EventLog:
        return self.robot.log


def intrabar_path(o: float, h: float, l: float, c: float, low_first: bool | None) -> list[float]:
    """Prices visited inside one bar. low_first: True -> low before high, False -> high before low,
    None -> bar-direction heuristic."""
    low_first = (c >= o) if low_first is None else low_first
    return [o, l, h, c] if low_first else [o, h, l, c]


def bar_ticks(symbol: str, t0: datetime, step: int, o: float, h: float, l: float, c: float, spread: float,
              low_first: bool | None) -> list[Tick]:
    path = intrabar_path(o, h, l, c, low_first)
    offs = [0, step / 3, 2 * step / 3, step - 1]
    half = spread / 2
    return [Tick(symbol, t0 + timedelta(seconds=dt), p - half, p + half, gap=(k == 0))
            for k, (p, dt) in enumerate(zip(path, offs))]


def run_backtest(settings: Settings, candles: pd.DataFrame, label: str = "", log_analysis: bool = False,
                 log: EventLog | None = None, progress=None, start: int = 0, end: int | None = None,
                 on_bar=None, path_candles: pd.DataFrame | None = None) -> BacktestResult:
    """Trade candles[start:end]. Candles before `start` only feed the indicators (walk-forward windows
    start with their indicators already warm, and never trade outside the window).
    `on_bar(i, robot)` is called after every processed candle.
    `path_candles`: finer candles (e.g. 1m or 5m) used as the price path INSIDE each candle where they
    exist, instead of the synthetic open-extreme-extreme-close path. Decisions still use closed candles of
    the strategy timeframe only."""
    candles, _ = validate(candles)
    sym, tf = settings.market.symbol, settings.market.timeframe
    inst = get_instrument(sym)
    step = TIMEFRAMES[tf]
    feats = featurize(candles, settings)
    provider = HistoricalDataProvider(candles, sym, tf, label or "historical")
    robot = Robot(settings, mode="BACKTEST", precomputed=feats, log=log or EventLog(keep=20_000),
                  log_analysis=log_analysis)
    spread = settings.execution.spread_override if settings.execution.spread_override is not None else inst.spread
    fine: dict = {}
    fine_step = 0
    if path_candles is not None and len(path_candles) > 1:
        pc, _ = validate(path_candles)
        fine_step = int((pc["timestamp"].diff().dropna().dt.total_seconds()).min())
        if fine_step >= step or step % fine_step:
            raise ValueError("path_candles must be a finer timeframe that divides the strategy timeframe")
        key = pc["timestamp"].dt.floor(f"{step}s")
        fine = {k: g for k, g in pc.groupby(key)}
    order = settings.execution.intrabar_order
    rng = np.random.default_rng(settings.execution.intrabar_seed)
    n = len(candles) if end is None else min(end, len(candles))
    if start > 0:
        robot.market.on_candle_close(candles.iloc[start - 1], start - 1)   # indicators as of the window start
    t_first = t_last = None
    for i in range(start, n):
        provider.cursor = i
        row = provider.bar(i)
        t0 = pd.Timestamp(row["timestamp"]).to_pydatetime()
        t_first = t_first or t0
        b, st = robot.strategy.basket, robot.strategy
        direction = b.direction if b is not None else None
        if direction is None and st.pending_open and robot.executor.pending():
            direction = robot.executor.pending()[0].direction
        low_first = None
        if direction is not None:
            adverse_first = order == "ADVERSE_FIRST" or (order == "RANDOM" and rng.random() < 0.5)
            low_first = (direction == "BUY") == adverse_first
        sub = fine.get(pd.Timestamp(row["timestamp"])) if fine else None
        if sub is not None and len(sub):
            ticks = []
            for j, fr in enumerate(sub.itertuples()):
                tks = bar_ticks(sym, fr.timestamp.to_pydatetime(), fine_step, fr.open, fr.high, fr.low, fr.close,
                                spread, low_first)
                ticks += tks if j == 0 else [Tick(t.symbol, t.time, t.bid, t.ask, gap=False) for t in tks]
            robot.path_bars = getattr(robot, "path_bars", 0) + 1
        else:
            ticks = bar_ticks(sym, t0, step, float(row["open"]), float(row["high"]), float(row["low"]),
                              float(row["close"]), spread, low_first)
        for tk in ticks:
            robot.process_tick(tk)
        robot.process_bar_close(row, index=i)
        t_last = t0 + timedelta(seconds=step)
        if on_bar is not None:
            on_bar(i, robot)
        if progress and i % 500 == 0:
            progress(i, n)
    # close what is still open at the last price, so its P&L is counted (and visible as END_OF_DATA)
    if robot.strategy.basket is not None and robot.last_tick is not None:
        last = robot.last_tick
        end_tick = Tick(sym, t_last, last.bid, last.ask, gap=True)
        it = robot.strategy.close_intent("END_OF_DATA", t_last, "backtest ended with the basket open")
        if it is not None:
            robot.log.add(t_last, "END_OF_DATA", f"Backtest ended: closing {it.basket_id} at the last price")
            robot.executor.cancel_all()
            robot.executor.submit(it, t_last - timedelta(days=1))   # due immediately, whatever the delay
            robot._apply(robot.executor.process(end_tick))
    return BacktestResult(settings, label, robot, candles.iloc[start:n].reset_index(drop=True),
                          feats.iloc[start:n].reset_index(drop=True), t_first, t_last)
