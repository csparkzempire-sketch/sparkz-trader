"""Study bundles shared by the API and the CLI, so both report exactly the same thing."""

from __future__ import annotations

from app.backtest.monte_carlo import perturbation_runs, sequence_tests
from app.backtest.runner import run_backtest
from app.backtest.walk_forward import period_stability, walk_forward
from app.config import Settings


def robustness_study(s: Settings, features, runs: int = 40) -> dict:
    base = run_backtest(s, features=features)
    return {"baseline": {k: base.metrics.get(k) for k in ("net_pnl", "return_pct", "max_drawdown_pct", "baskets", "profit_factor")},
            "sequence": sequence_tests([b.pnl for b in base.baskets], s.risk.initial_capital, s.risk.max_account_drawdown_percent),
            "perturbation": perturbation_runs(s, features, n=runs)}


def walk_forward_study(s: Settings, features) -> dict:
    period = "W-MON" if s.market.timeframe in ("15m", "30m") else "MS"
    return {"walk_forward": walk_forward(s, features), "periods": period_stability(s, features, period)}
