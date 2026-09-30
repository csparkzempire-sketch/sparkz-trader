import React, { createContext, useContext, useEffect, useState } from "react";

// App-wide selections kept across pages (and across reloads, when storage is available).
interface AppState {
  symbol: string;
  setSymbol: (s: string) => void;
  timeframe: string;
  setTimeframe: (t: string) => void;
  backtestId: number | null;
  setBacktestId: (id: number | null) => void;
  account: string | null;
  setAccount: (a: string | null) => void;
}

const Ctx = createContext<AppState | null>(null);

function stored<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : (JSON.parse(v) as T);
  } catch {
    return fallback;
  }
}

function useStored<T>(key: string, fallback: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => stored(key, fallback));
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(v));
    } catch {
      /* private mode: selections just aren't remembered */
    }
  }, [key, v]);
  return [v, setV];
}

export function AppStateProvider({ children }: { children: React.ReactNode }) {
  const [symbol, setSymbol] = useStored("grid.symbol", "XAUUSD");
  const [timeframe, setTimeframe] = useStored("grid.timeframe", "15m");
  const [backtestId, setBacktestId] = useStored<number | null>("grid.backtest", null);
  const [account, setAccount] = useStored<string | null>("grid.account", null);
  return (
    <Ctx.Provider value={{ symbol, setSymbol, timeframe, setTimeframe, backtestId, setBacktestId, account, setAccount }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAppState(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAppState outside AppStateProvider");
  return v;
}

export const SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD"];
export const TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"];

/** Small hook: run an async loader, track loading and error. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setLoading(true);
    fn()
      .then((d) => live && (setData(d), setError(null)))
      .catch((e) => live && setError(String(e.message ?? e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}
