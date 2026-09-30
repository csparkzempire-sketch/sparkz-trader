import type {
  Analysis,
  BacktestListItem,
  BacktestSummary,
  CandleRow,
  CompareRow,
  Inventory,
  PaperListItem,
  PaperStatus,
  Preset,
} from "../types/api";

const BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.json();
}

const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export interface RunBody {
  preset?: string | null;
  overrides?: Record<string, any>;
  name?: string;
  save?: boolean;
}

export const api = {
  health: () => request<{ status: string; live_trading_enabled: boolean }>("/health"),
  inventory: () => request<Inventory[]>("/market/inventory"),
  download: (symbol: string, timeframe: string) => post<Record<string, any>>("/market/download", { symbol, timeframe }),
  candles: (symbol: string, timeframe: string, limit = 300) =>
    request<CandleRow[]>(`/market/candles?symbol=${symbol}&timeframe=${timeframe}&limit=${limit}`),
  analysis: (symbol: string, timeframe: string) => request<Analysis>(`/market/analysis?symbol=${symbol}&timeframe=${timeframe}`),
  presets: () => request<Preset[]>("/strategy/presets"),
  defaultConfig: () => request<Record<string, any>>("/strategy/config"),
  runBacktest: (b: RunBody) => post<BacktestSummary>("/backtest/run", b),
  listBacktests: () => request<BacktestListItem[]>("/backtest/list"),
  getBacktest: (id: number) => request<BacktestSummary>(`/backtest/${id}`),
  compare: (presets: string[], shared: Record<string, any>) =>
    post<{ symbol: string; timeframe: string; rows: CompareRow[]; note: string }>("/backtest/compare", { presets, shared }),
  stress: (b: RunBody) => post<Record<string, any>>("/backtest/stress", b),
  robustness: (b: RunBody & { runs: number }) => post<Record<string, any>>("/backtest/robustness", b),
  sensitivity: (b: RunBody) => post<Record<string, any[]>>("/backtest/sensitivity", b),
  walkForward: (b: RunBody) => post<Record<string, any>>("/backtest/walk-forward", b),
  paperAccounts: () => request<PaperListItem[]>("/paper/accounts"),
  paperCreate: (name: string, preset: string | null, overrides: Record<string, any>) =>
    post<PaperStatus>("/paper/accounts", { name, preset, overrides }),
  paperStep: (name: string) => post<Record<string, any>>(`/paper/${name}/step`, {}),
  paperStatus: (name: string) => request<PaperStatus>(`/paper/${name}`),
  paperResume: (name: string) => post<{ result: string }>(`/paper/${name}/resume`, {}),
  riskLimits: () => request<Record<string, any>>("/risk/limits"),
  riskStatus: () => request<Record<string, any>[]>("/risk/status"),
};
