export interface Candle {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  regime: string | null;
  ema_20: number | null;
  ema_50: number | null;
  ema_200: number | null;
  rsi: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  atr: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
}

export interface MarketDataResponse {
  symbol: string;
  timeframe: string;
  count: number;
  candles: Candle[];
}

export interface LatestPriceResponse {
  symbol: string;
  timeframe: string;
  timestamp: string;
  close: number;
  regime: string | null;
}

export interface BacktestRequest {
  symbol: string;
  timeframe: string;
  initial_capital: number;
  risk_per_trade: number;
  stop_atr_multiplier: number;
  take_profit_r: number;
  spread_pips: number;
  slippage_pips: number;
  strategy: string;
  max_simultaneous_positions?: number; // omit to use the server default (1)
}

export interface PerformanceMetrics {
  total_return_pct: number;
  cagr_pct: number | null;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate_pct: number;
  average_win: number;
  average_loss: number;
  expectancy: number;
  profit_factor: number | null;
  max_drawdown_pct: number;
  average_holding_bars: number;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  annualized_volatility_pct: number | null;
  exposure_pct: number;
  turnover: number;
}

export interface TradeRecord {
  direction: string;
  pnl: number;
  reason: string;
  entry_time: string;
  exit_time: string;
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
}

export interface DrawdownPoint {
  timestamp: string;
  drawdown_pct: number;
}

export interface BacktestResponse {
  backtest_id: string;
  symbol: string;
  timeframe: string;
  config: Record<string, unknown>;
  metrics: PerformanceMetrics;
  baseline_comparison: Record<string, unknown>;
  equity_curve: EquityPoint[];
  drawdown_curve: DrawdownPoint[];
  monthly_returns: { month: string; return_pct: number }[];
  trades: TradeRecord[];
  warnings: string[];
}

export interface TrainModelRequest {
  symbol: string;
  timeframe: string;
  model_type: string;
  hyperparameters: Record<string, unknown>;
}

export interface ClassificationMetrics {
  accuracy: number;
  precision: number;
  recall: number;
  f1: number;
  roc_auc: number | null;
  n_samples: number;
  positive_rate: number;
}

export interface TrainModelResponse {
  model_id: string;
  model_type: string;
  train_period: string[];
  validation_period: string[];
  test_period: string[];
  n_features: number;
  classification_metrics_validation: ClassificationMetrics;
  classification_metrics_test: ClassificationMetrics;
  feature_importance: { feature: string; importance: number }[];
  threshold_sweep: { threshold: number; n_signals: number; precision: number | null }[];
  disclaimer: string;
}

export interface PredictResponse {
  model_id: string;
  symbol: string;
  timestamp: string;
  probability_up: number;
  probability_down: number;
  signal: "BUY" | "SELL" | "HOLD";
  explanation: string;
}

export interface ModelSummary {
  model_id: string;
  model_type: string;
  symbol: string;
  timeframe: string;
  created_at: string;
  metrics: Record<string, unknown>;
}

export interface PaperAccountResponse {
  account_name: string;
  balance: number;
  equity: number;
  is_active: boolean;
  open_positions: number;
}

export interface PaperPosition {
  symbol: string;
  direction: string;
  entry_price: number;
  stop_price: number;
  target_price: number;
  size: number;
  opened_at: string;
}

export interface PaperTrade {
  symbol: string;
  direction: string;
  entry_price: number;
  exit_price: number;
  size: number;
  pnl: number;
  opened_at: string;
  closed_at: string;
  reason: string;
}
