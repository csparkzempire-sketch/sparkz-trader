import type {
  BacktestRequest,
  BacktestResponse,
  LatestPriceResponse,
  MarketDataResponse,
  ModelSummary,
  PaperAccountResponse,
  PaperPosition,
  PaperTrade,
  PredictResponse,
  TrainModelRequest,
  TrainModelResponse,
} from "../types/api";

const BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore parse errors */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  health: () => request<{ status: string; live_trading_enabled: boolean }>("/health"),

  getMarketData: (symbol: string, timeframe: string, limit = 500) =>
    request<MarketDataResponse>(`/market/${encodeURIComponent(symbol)}?timeframe=${timeframe}&limit=${limit}`),

  getLatestPrice: (symbol: string, timeframe: string) =>
    request<LatestPriceResponse>(`/market/${encodeURIComponent(symbol)}/latest?timeframe=${timeframe}`),

  runBacktest: (req: BacktestRequest) =>
    request<BacktestResponse>("/backtest", { method: "POST", body: JSON.stringify(req) }),

  getBacktest: (id: string) => request<BacktestResponse>(`/backtest/${id}`),

  trainModel: (req: TrainModelRequest) =>
    request<TrainModelResponse>("/models/train", { method: "POST", body: JSON.stringify(req) }),

  predict: (model_id: string, symbol: string, timeframe: string) =>
    request<PredictResponse>("/models/predict", {
      method: "POST",
      body: JSON.stringify({ model_id, symbol, timeframe }),
    }),

  listModels: () => request<ModelSummary[]>("/models"),

  paperStart: (account_name: string, starting_balance: number) =>
    request<PaperAccountResponse>("/paper/start", {
      method: "POST",
      body: JSON.stringify({ account_name, starting_balance }),
    }),

  paperStop: (account_name: string) =>
    request<PaperAccountResponse>(`/paper/stop?account_name=${encodeURIComponent(account_name)}`, { method: "POST" }),

  paperAccount: (account_name: string) =>
    request<PaperAccountResponse>(`/paper/account?account_name=${encodeURIComponent(account_name)}`),

  paperPositions: (account_name: string) =>
    request<PaperPosition[]>(`/paper/positions?account_name=${encodeURIComponent(account_name)}`),

  paperTrades: (account_name: string) =>
    request<PaperTrade[]>(`/paper/trades?account_name=${encodeURIComponent(account_name)}`),
};
