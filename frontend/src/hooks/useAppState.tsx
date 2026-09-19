import React, { createContext, useContext, useState } from "react";
import type { BacktestResponse } from "../types/api";

interface AppState {
  symbol: string;
  setSymbol: (s: string) => void;
  timeframe: string;
  setTimeframe: (t: string) => void;
  lastModelId: string | null;
  setLastModelId: (id: string | null) => void;
  lastBacktest: BacktestResponse | null;
  setLastBacktest: (b: BacktestResponse | null) => void;
  paperAccountName: string;
}

const AppStateContext = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: React.ReactNode }) {
  const [symbol, setSymbol] = useState("EURUSD=X");
  const [timeframe, setTimeframe] = useState("1h");
  const [lastModelId, setLastModelId] = useState<string | null>(null);
  const [lastBacktest, setLastBacktest] = useState<BacktestResponse | null>(null);

  return (
    <AppStateContext.Provider
      value={{
        symbol,
        setSymbol,
        timeframe,
        setTimeframe,
        lastModelId,
        setLastModelId,
        lastBacktest,
        setLastBacktest,
        paperAccountName: "default",
      }}
    >
      {children}
    </AppStateContext.Provider>
  );
}

export function useAppState() {
  const ctx = useContext(AppStateContext);
  if (!ctx) throw new Error("useAppState must be used within AppStateProvider");
  return ctx;
}
