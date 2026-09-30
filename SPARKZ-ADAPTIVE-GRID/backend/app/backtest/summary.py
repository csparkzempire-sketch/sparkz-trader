"""The compact metric set shown in comparison, stress and robustness tables."""

from __future__ import annotations

KEY_METRICS = [
    "baskets", "win_rate_pct", "net_pnl", "return_pct", "profit_factor", "expectancy", "max_drawdown_pct",
    "avg_basket_profit", "avg_basket_loss", "largest_basket_loss", "max_consecutive_losses",
    "avg_positions_per_basket", "baskets_at_max_positions", "max_effective_leverage", "max_margin_usage_pct",
    "time_in_market_pct", "halted",
]
MAE_METRICS = ["worst_mae", "avg_winner_profit", "avg_winner_mae", "median_winner_profit_to_mae",
               "winners_with_mae_over_1x_profit_pct", "largest_loss_in_avg_wins"]


def pick(metrics: dict) -> dict:
    row = {k: metrics.get(k) for k in KEY_METRICS}
    mm = metrics.get("mae_mfe") or {}
    row.update({k: mm.get(k) for k in MAE_METRICS})
    return row
