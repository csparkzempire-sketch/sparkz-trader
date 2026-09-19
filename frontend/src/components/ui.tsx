import React from "react";
import { useAppState } from "../hooks/useAppState";

export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="px-6 pt-6 pb-4">
      <h1 className="text-xl font-bold text-base-text">{title}</h1>
      {subtitle && <p className="text-sm text-base-muted mt-1">{subtitle}</p>}
    </div>
  );
}

export function StatCard({
  label,
  value,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: string;
  tone?: "up" | "down" | "neutral";
  hint?: string;
}) {
  const toneClass = tone === "up" ? "text-accent-up" : tone === "down" ? "text-accent-down" : "text-base-text";
  return (
    <div className="panel p-4">
      <div className="stat-label">{label}</div>
      <div className={`stat-value mt-1 ${toneClass}`}>{value}</div>
      {hint && <div className="text-[11px] text-base-muted mt-1">{hint}</div>}
    </div>
  );
}

export function SignalBadge({ signal }: { signal: string }) {
  const cls = signal === "BUY" ? "badge-buy" : signal === "SELL" ? "badge-sell" : "badge-hold";
  return <span className={`badge ${cls}`}>{signal}</span>;
}

export function RegimeBadge({ regime }: { regime: string | null }) {
  if (!regime) return <span className="text-base-muted text-xs">—</span>;
  const colorMap: Record<string, string> = {
    TRENDING_UP: "text-accent-up",
    TRENDING_DOWN: "text-accent-down",
    RANGING: "text-base-muted",
    HIGH_VOLATILITY: "text-amber-400",
    LOW_VOLATILITY: "text-sky-400",
  };
  return <span className={`text-xs font-mono-nums font-semibold ${colorMap[regime] ?? "text-base-text"}`}>{regime}</span>;
}

export function SymbolTimeframePicker() {
  const { symbol, setSymbol, timeframe, setTimeframe } = useAppState();
  return (
    <div className="flex items-center gap-3">
      <select
        value={symbol}
        onChange={(e) => setSymbol(e.target.value)}
        className="bg-base-bg border border-base-border rounded px-2 py-1.5 text-sm text-base-text"
      >
        <option value="EURUSD=X">EUR/USD</option>
        <option value="GBPUSD=X">GBP/USD</option>
        <option value="USDJPY=X">USD/JPY</option>
      </select>
      <select
        value={timeframe}
        onChange={(e) => setTimeframe(e.target.value)}
        className="bg-base-bg border border-base-border rounded px-2 py-1.5 text-sm text-base-text"
      >
        <option value="1h">1H</option>
        <option value="1d">1D</option>
        <option value="15m">15M</option>
      </select>
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="panel border-accent-down/40 bg-accent-down/10 p-3 text-sm text-accent-down">
      {message}
    </div>
  );
}

export function LoadingBlock({ label = "Loading…" }: { label?: string }) {
  return <div className="panel p-8 text-center text-sm text-base-muted">{label}</div>;
}

export function DisclaimerNote({ children }: { children: React.ReactNode }) {
  return <p className="text-[11px] text-base-muted mt-2 leading-relaxed">{children}</p>;
}
