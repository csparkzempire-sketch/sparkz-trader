"""
Backtest orchestration: data -> features -> engine -> metrics -> (optionally) SQLite.

Indicators are computed on the full history, then the engine trades only
from `market.start` on, so the first traded bar already has warmed-up
indicators without peeking past it (every feature is trailing).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import pandas as pd

from app.backtest.engine import GridEngine
from app.backtest.metrics import compute_metrics, downsample_curve
from app.config import Settings
from app.data.repository import load_candles
from app.models.database import (Backtest, BacktestMetric, BasketPosition, CompletedBasket, EquitySnapshot,
                                 ParameterSet, RiskEvent, StrategySignal, session_factory)
from app.strategy.basket_manager import CompletedBasketRecord
from app.strategy.market_analysis import compute_features
from app.strategy.regime_detector import label_regimes


@dataclass
class BacktestResult:
    settings: Settings
    metrics: dict
    baskets: list[CompletedBasketRecord]
    curve: list
    signals: list[dict]
    risk_events: list[dict]
    data_info: dict = field(default_factory=dict)
    backtest_id: int | None = None

    def summary(self, max_points: int = 1500) -> dict:
        return {"metrics": self.metrics, "curve": downsample_curve(self.curve, max_points),
                "baskets": [b.to_dict() for b in self.baskets], "risk_events": self.risk_events[-500:],
                "signals": self.signals[-500:], "config": self.settings.model_dump(mode="json"), "data": self.data_info}


def prepare_features(candles: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    return label_regimes(compute_features(candles, settings.analysis), settings.analysis).reset_index(drop=True)


def run_backtest(settings: Settings, candles: pd.DataFrame | None = None, features: pd.DataFrame | None = None,
                 save: bool = False, name: str | None = None, kind: str = "backtest", db=None) -> BacktestResult:
    m = settings.market
    if features is None:
        if candles is None:
            candles = load_candles(m.symbol, m.timeframe, db=db)
        if candles.empty:
            raise ValueError(f"No stored candles for {m.symbol} {m.timeframe}. Download or import data first.")
        features = prepare_features(candles, settings)
    f = features
    start_i = 1
    if m.start:
        start_i = max(1, int(f["timestamp"].searchsorted(pd.Timestamp(m.start, tz="UTC"))))
    if m.end:
        f = f[f["timestamp"] < pd.Timestamp(m.end, tz="UTC")].reset_index(drop=True)
    if start_i >= len(f):
        raise ValueError("No bars in the requested date range")

    eng = GridEngine(settings)
    for i in range(start_i, len(f)):
        eng.process_bar(f, i)
    eng.finish(f)
    st = eng.state
    metrics = compute_metrics(st.completed, st.curve, settings.risk.initial_capital, m.symbol, m.timeframe)
    metrics["baskets_at_max_positions"] = sum(1 for b in st.completed if b.positions >= settings.risk.max_positions)
    metrics["risk_event_counts"] = pd.Series([e["kind"] for e in st.risk_events]).value_counts().to_dict() \
        if st.risk_events else {}
    metrics["halted"] = eng.risk.state.halted
    res = BacktestResult(settings, metrics, st.completed, st.curve, st.signals, st.risk_events,
                         {"bars": len(f) - start_i, "first": f["timestamp"].iloc[start_i].isoformat(),
                          "last": f["timestamp"].iloc[-1].isoformat()})
    if save:
        res.backtest_id = save_result(res, name or settings.name, kind, db)
    return res


def _param_set(s, settings: Settings) -> int:
    cfg = settings.model_dump(mode="json")
    h = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    row = s.query(ParameterSet).filter_by(config_hash=h).one_or_none()
    if row is None:
        row = ParameterSet(name=settings.name, config_hash=h, config=cfg)
        s.add(row)
        s.flush()
    return row.id


def save_result(res: BacktestResult, name: str, kind: str = "backtest", db=None) -> int:
    Session = session_factory(db)
    m = res.settings.market
    with Session() as s, s.begin():
        bt = Backtest(name=name, kind=kind, symbol=m.symbol, timeframe=m.timeframe,
                      start=pd.Timestamp(res.data_info["first"]).to_pydatetime(),
                      end=pd.Timestamp(res.data_info["last"]).to_pydatetime(), bars=res.data_info["bars"],
                      parameter_set_id=_param_set(s, res.settings), summary=res.summary())
        s.add(bt)
        s.flush()
        for k, v in res.metrics.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                s.add(BacktestMetric(backtest_id=bt.id, name=k, value=float(v)))
        for b in res.baskets:
            s.add(CompletedBasket(backtest_id=bt.id, basket_uid=b.uid, direction=b.direction, opened_at=b.opened_at,
                                  closed_at=b.closed_at, positions=b.positions, total_lots=b.total_lots,
                                  avg_entry=b.avg_entry, exit_price=b.exit_price, pnl=b.pnl, pnl_pct=b.pnl_pct,
                                  mae=b.mae, mfe=b.mfe, bars_held=b.bars_held, regime=b.regime, vol_regime=b.vol_regime,
                                  close_reason=b.close_reason, max_notional=b.max_notional, max_margin=b.max_margin))
            for e in b.entries:
                s.add(BasketPosition(backtest_id=bt.id, basket_uid=b.uid, seq=e["seq"], direction=b.direction,
                                     lots=e["lots"], entry_time=pd.Timestamp(e["time"]).to_pydatetime(),
                                     entry_price=e["price"], exit_time=b.closed_at, exit_price=b.exit_price))
        for e in res.risk_events:
            s.add(RiskEvent(backtest_id=bt.id, ts=pd.Timestamp(e["ts"]).to_pydatetime(), kind=e["kind"], detail=e["detail"]))
        for g in res.signals:
            s.add(StrategySignal(backtest_id=bt.id, ts=pd.Timestamp(g["ts"]).to_pydatetime(), signal=g["signal"],
                                 regime=g["regime"], vol_regime=g["vol_regime"], acted=g["acted"], reason=g["reason"]))
        for p in downsample_curve(res.curve):
            s.add(EquitySnapshot(backtest_id=bt.id, ts=pd.Timestamp(p["t"]).to_pydatetime(), balance=p["balance"],
                                 equity=p["equity"], drawdown_pct=p["drawdown_pct"], open_positions=p["positions"]))
        return bt.id
