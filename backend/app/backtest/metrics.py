"""Performance analytics for a completed backtest."""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from app.backtest.portfolio import Portfolio

TRADING_PERIODS_PER_YEAR = {
    "1m": 252 * 24 * 60,
    "5m": 252 * 24 * 12,
    "15m": 252 * 24 * 4,
    "30m": 252 * 24 * 2,
    "1h": 252 * 24,
    "4h": 252 * 6,
    "1d": 252,
}


@dataclass
class PerformanceMetrics:
    total_return_pct: float
    cagr_pct: float | None
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    average_win: float
    average_loss: float
    expectancy: float
    profit_factor: float | None
    max_drawdown_pct: float
    average_holding_bars: float
    sharpe_ratio: float | None
    sortino_ratio: float | None
    annualized_volatility_pct: float | None
    exposure_pct: float
    turnover: float

    def as_dict(self) -> dict:
        return asdict(self)


def _equity_series(portfolio: Portfolio) -> pd.Series:
    if not portfolio.equity_curve:
        return pd.Series(dtype=float)
    idx, vals = zip(*portfolio.equity_curve)
    return pd.Series(vals, index=pd.to_datetime(list(idx)))


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    return float(drawdown.min()) if not drawdown.empty else 0.0


def compute_metrics(portfolio: Portfolio, timeframe: str) -> PerformanceMetrics:
    equity = _equity_series(portfolio)
    initial = portfolio.initial_capital
    final = equity.iloc[-1] if not equity.empty else initial

    total_return_pct = (final / initial - 1.0) * 100 if initial > 0 else 0.0

    periods_per_year = TRADING_PERIODS_PER_YEAR.get(timeframe)
    cagr_pct = None
    if periods_per_year and len(equity) > 1 and initial > 0 and final > 0:
        n_periods = len(equity)
        years = n_periods / periods_per_year
        if years > 0:
            cagr_pct = ((final / initial) ** (1 / years) - 1) * 100

    trades = portfolio.closed_trades
    total_trades = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate_pct = (len(wins) / total_trades * 100) if total_trades else 0.0
    average_win = float(np.mean([t.pnl for t in wins])) if wins else 0.0
    average_loss = float(np.mean([t.pnl for t in losses])) if losses else 0.0
    expectancy = float(np.mean([t.pnl for t in trades])) if trades else 0.0

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (None if gross_profit == 0 else float("inf"))

    max_dd_pct = _max_drawdown(equity) * 100

    holding_bars = []
    for t in trades:
        # holding time expressed in number of bars is approximated via
        # timeframe-aware duration; callers with bar-index data can refine this.
        pass
    average_holding_bars = float(
        np.mean([(t.exit_time - t.entry_time).total_seconds() for t in trades])
    ) if trades else 0.0

    returns = equity.pct_change().dropna() if not equity.empty else pd.Series(dtype=float)
    sharpe_ratio = None
    sortino_ratio = None
    annualized_vol_pct = None
    if periods_per_year and len(returns) > 1 and returns.std() > 0:
        sharpe_ratio = float((returns.mean() / returns.std()) * np.sqrt(periods_per_year))
        annualized_vol_pct = float(returns.std() * np.sqrt(periods_per_year) * 100)
        downside = returns[returns < 0]
        if len(downside) > 0 and downside.std() > 0:
            sortino_ratio = float((returns.mean() / downside.std()) * np.sqrt(periods_per_year))

    total_bars = len(equity)
    bars_in_position = 0  # exact bar-level exposure requires the bar loop; approximate via trade duration below
    exposure_pct = 0.0
    if total_bars > 0 and trades:
        approx_bars_per_trade = [
            max(1, int((t.exit_time - t.entry_time).total_seconds() / 3600)) for t in trades  # rough, hourly-bar assumption
        ]
        exposure_pct = min(100.0, (sum(approx_bars_per_trade) / total_bars) * 100)

    turnover = float(sum(abs(t.size * t.entry_price) for t in trades) / initial) if initial > 0 else 0.0

    return PerformanceMetrics(
        total_return_pct=round(total_return_pct, 4),
        cagr_pct=round(cagr_pct, 4) if cagr_pct is not None else None,
        total_trades=total_trades,
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate_pct=round(win_rate_pct, 2),
        average_win=round(average_win, 2),
        average_loss=round(average_loss, 2),
        expectancy=round(expectancy, 2),
        profit_factor=round(profit_factor, 3) if isinstance(profit_factor, float) and profit_factor != float("inf") else profit_factor,
        max_drawdown_pct=round(max_dd_pct, 4),
        average_holding_bars=round(average_holding_bars / 3600, 2),  # hours, given hourly assumption above
        sharpe_ratio=round(sharpe_ratio, 3) if sharpe_ratio is not None else None,
        sortino_ratio=round(sortino_ratio, 3) if sortino_ratio is not None else None,
        annualized_volatility_pct=round(annualized_vol_pct, 3) if annualized_vol_pct is not None else None,
        exposure_pct=round(exposure_pct, 2),
        turnover=round(turnover, 4),
    )


def equity_curve_as_records(portfolio: Portfolio) -> list[dict]:
    return [{"timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts), "equity": eq} for ts, eq in portfolio.equity_curve]


def drawdown_curve_as_records(portfolio: Portfolio) -> list[dict]:
    equity = _equity_series(portfolio)
    if equity.empty:
        return []
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    return [{"timestamp": ts.isoformat(), "drawdown_pct": float(dd) * 100} for ts, dd in drawdown.items()]


def monthly_returns(portfolio: Portfolio) -> list[dict]:
    equity = _equity_series(portfolio)
    if equity.empty:
        return []
    monthly = equity.tz_convert("UTC").tz_localize(None).resample("ME").last().pct_change().dropna() * 100
    return [{"month": str(idx.to_period("M")), "return_pct": float(val)} for idx, val in monthly.items()]


def trade_distribution(portfolio: Portfolio) -> list[dict]:
    return [
        {
            "direction": t.direction,
            "pnl": round(t.pnl, 2),
            "reason": t.reason,
            "entry_time": t.entry_time.isoformat(),
            "exit_time": t.exit_time.isoformat(),
        }
        for t in portfolio.closed_trades
    ]


def compare_to_buy_and_hold(df: pd.DataFrame, initial_capital: float) -> dict:
    """Baseline comparison: simple buy-and-hold over the same period."""
    if df.empty:
        return {}
    start_price = df["close"].iloc[0]
    end_price = df["close"].iloc[-1]
    bh_return_pct = (end_price / start_price - 1) * 100
    return {
        "buy_and_hold_return_pct": round(bh_return_pct, 4),
        "buy_and_hold_final_equity": round(initial_capital * (end_price / start_price), 2),
        "note": "Buy-and-hold ignores transaction costs and is shown for directional context only, not as a superiority claim.",
    }
