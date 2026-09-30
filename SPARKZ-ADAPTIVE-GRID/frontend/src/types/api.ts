// Shapes returned by the research API (backend/app/api). Numbers can be null when a figure
// isn't defined (e.g. profit factor with no losing baskets is "inf").

export type Num = number | null;

export interface CurvePoint {
  t: string;
  equity: number;
  balance: number;
  drawdown_pct: number;
  positions: number;
}

export interface GroupStats {
  baskets: number;
  win_rate_pct: number;
  net_pnl: number;
  avg_pnl: number;
  profit_factor: number | string | null;
  worst_basket: number;
  avg_mae: number;
  avg_positions: number;
}

export interface MaeBlock {
  avg_mae: Num;
  worst_mae: Num;
  avg_mfe: Num;
  avg_winner_profit: Num;
  avg_winner_mae: Num;
  median_winner_profit_to_mae: Num;
  winners_with_mae_over_1x_profit_pct: Num;
  winners_with_mae_over_3x_profit_pct: Num;
  worst_mae_in_avg_wins: Num;
  largest_loss_in_avg_wins: Num;
  scatter: { uid: string; pnl: number; mae: number; mfe: number; positions: number }[];
}

export interface Metrics {
  symbol: string;
  timeframe: string;
  start: string | null;
  end: string | null;
  bars: number;
  starting_balance: number;
  ending_balance: number;
  net_pnl: number;
  return_pct: number;
  baskets: number;
  winning_baskets: number;
  losing_baskets: number;
  win_rate_pct: Num;
  avg_basket_profit: Num;
  avg_basket_loss: Num;
  largest_basket_profit: Num;
  largest_basket_loss: Num;
  profit_factor: number | string | null;
  expectancy: Num;
  max_drawdown_pct: number;
  max_drawdown_usd: number;
  max_consecutive_losses: number;
  avg_basket_bars: Num;
  max_basket_bars: Num;
  max_positions_in_basket: number;
  avg_positions_per_basket: Num;
  baskets_at_max_positions: number;
  max_notional: number;
  max_effective_leverage: number;
  max_margin_usage_pct: number;
  time_in_market_pct: number;
  sharpe: Num;
  sortino: Num;
  close_reasons: Record<string, number>;
  positions_histogram: Record<string, number>;
  mae_mfe: MaeBlock;
  by_regime: Record<string, GroupStats>;
  by_vol_regime: Record<string, GroupStats>;
  monthly: { month: string; pnl: number; return_pct: number }[];
  pnl_distribution: { from: number; to: number; count: number }[];
  risk_event_counts: Record<string, number>;
  halted: string | null;
}

export interface BasketRecord {
  uid: string;
  direction: string;
  opened_at: string;
  closed_at: string;
  positions: number;
  total_lots: number;
  avg_entry: number;
  exit_price: number;
  pnl: number;
  pnl_pct: number;
  mae: number;
  mfe: number;
  bars_held: number;
  regime: string;
  vol_regime: string;
  close_reason: string;
  entries: { seq: number; lots: number; price: number; time: string }[];
}

export interface RiskEvent {
  ts: string;
  kind: string;
  detail: string;
}

export interface BacktestSummary {
  id: number | null;
  name?: string;
  metrics: Metrics;
  curve: CurvePoint[];
  baskets: BasketRecord[];
  risk_events: RiskEvent[];
  config: Record<string, any>;
  data: { bars: number; first: string; last: string };
}

export interface BacktestListItem {
  id: number;
  name: string;
  kind: string;
  symbol: string;
  timeframe: string;
  created_at: string;
  start: string | null;
  end: string | null;
  bars: number;
  net_pnl: Num;
  baskets: Num;
  max_drawdown_pct: Num;
}

export interface Preset {
  key: string;
  name: string;
  config: Record<string, any>;
}

export interface CompareRow extends Record<string, any> {
  key: string;
  name: string;
  high_risk: boolean;
  curve: { t: string; equity: number }[];
  by_regime: Record<string, GroupStats>;
}

export interface Analysis {
  timestamp: string;
  regime: string;
  vol_regime: string;
  signal: string;
  reasons: string[];
  features: Record<string, Num>;
}

export interface CandleRow {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  ema_fast: Num;
  ema_slow: Num;
  ema_trend: Num;
  bb_upper: Num;
  bb_lower: Num;
  rsi: Num;
  atr: Num;
  adx: Num;
  macd: Num;
  macd_signal: Num;
  macd_hist: Num;
  regime: string;
  vol_regime: string;
}

export interface PaperBasket {
  basket_id: string;
  direction: string;
  positions: number;
  total_lots: number;
  average_entry: number;
  opened_at: string;
  loss_limit: number;
  target_usd: Num;
  target_distance: Num;
  mae: number;
  mfe: number;
  entries: { seq: number; lots: number; entry_price: number; entry_time: string; ref_price: number }[];
}

export interface PaperStatus {
  name: string;
  symbol: string;
  timeframe: string;
  strategy: string;
  starting_balance: number;
  balance: number;
  equity: number;
  pnl: number;
  drawdown_pct: number;
  halted: string | null;
  last_bar: string | null;
  open_basket: PaperBasket | null;
  exposure_leverage: number;
  margin_usage_pct: number;
  baskets: { uid: string; direction: string; opened_at: string; closed_at: string; positions: number; pnl: number; mae: number; close_reason: string }[];
  risk_events: RiskEvent[];
  equity_curve: { t: string; equity: number; balance: number }[];
  limits: Record<string, number>;
}

export interface PaperListItem {
  name: string;
  symbol: string;
  timeframe: string;
  balance: number;
  equity: number;
  halted: string | null;
  last_bar: string | null;
  strategy: string;
}

export interface Inventory {
  symbol: string;
  timeframe: string;
  bars: number;
  first: string | null;
  last: string | null;
  source: string;
}
