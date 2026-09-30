import React from "react";

export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: string; right?: React.ReactNode }) {
  return (
    <div className="px-4 md:px-6 pt-6 md:pt-8 pb-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-3xl md:text-[2rem] leading-tight text-base-text">{title}</h1>
          {subtitle && <p className="text-sm text-base-muted mt-1.5 max-w-3xl">{subtitle}</p>}
        </div>
        {right}
      </div>
      <div className="mt-4 border-t border-base-text/80" aria-hidden="true" />
    </div>
  );
}

export function Page({ children }: { children: React.ReactNode }) {
  return <div className="px-4 md:px-6 pb-10 grid gap-5">{children}</div>;
}

export function StatCard({ label, value, tone = "neutral", hint }: { label: string; value: string; tone?: "up" | "down" | "neutral"; hint?: string }) {
  const cls = tone === "up" ? "text-accent-up" : tone === "down" ? "text-accent-down" : "text-base-text";
  return (
    <div className="panel p-3 md:p-4 min-w-0">
      <div className="stat-label">{label}</div>
      <div className={`stat-value mt-1 break-words ${cls}`}>{value}</div>
      {hint && <div className="text-[11px] text-base-muted mt-1">{hint}</div>}
    </div>
  );
}

export function StatGrid({ children, cols = 4 }: { children: React.ReactNode; cols?: 2 | 3 | 4 | 5 | 6 }) {
  const c = { 2: "md:grid-cols-2", 3: "md:grid-cols-3", 4: "md:grid-cols-4", 5: "md:grid-cols-5", 6: "md:grid-cols-6" }[cols];
  return <div className={`grid grid-cols-2 ${c} gap-3 items-start`}>{children}</div>;
}

export function Panel({ title, right, children, className = "" }: { title?: string; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`panel min-w-0 ${className}`}>
      {title && (
        <div className="panel-header flex flex-wrap items-center justify-between gap-2">
          <span>{title}</span>
          {right && <span className="normal-case tracking-normal font-normal">{right}</span>}
        </div>
      )}
      <div className="p-4 md:p-5">{children}</div>
    </section>
  );
}

export function Table({ head, rows, align, empty = "Nothing yet." }: { head: string[]; rows: React.ReactNode[][]; align?: ("l" | "r")[]; empty?: string }) {
  if (!rows.length) return <Empty>{empty}</Empty>;
  return (
    <div className="overflow-x-auto -mx-1">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-base-border text-left text-base-muted text-[11px] uppercase tracking-wider">
            {head.map((h, i) => (
              <th key={h + i} className={`px-2 py-2 whitespace-nowrap font-semibold ${align?.[i] === "r" ? "text-right" : ""}`}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, r) => (
            <tr key={r} className="border-b border-base-border/50 font-mono-nums">
              {cells.map((c, i) => (
                <td key={i} className={`px-2 py-2 whitespace-nowrap ${align?.[i] === "r" ? "text-right" : ""}`}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="text-sm text-base-muted">{children}</div>;
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="panel p-3 text-sm text-accent-down border-accent-down/60">{error}</div>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="text-sm text-base-muted">{what}…</div>;
}

export function Direction({ d }: { d: string }) {
  const cls = d === "BUY" ? "badge-buy" : d === "SELL" ? "badge-sell" : "badge-hold";
  return <span className={`badge ${cls}`}>{d}</span>;
}

const REGIME_TONE: Record<string, string> = {
  TRENDING_UP: "text-accent-up",
  TRENDING_DOWN: "text-accent-down",
  RANGING: "text-base-text",
  UNCERTAIN: "text-base-muted",
  HIGH_VOLATILITY: "text-accent-warn",
  LOW_VOLATILITY: "text-accent-oxford",
  NORMAL_VOLATILITY: "text-base-muted",
};

export function Regime({ r }: { r: string | null | undefined }) {
  if (!r) return <span className="text-base-muted">—</span>;
  return <span className={`text-xs font-semibold tracking-wide ${REGIME_TONE[r] ?? "text-base-text"}`}>{r.replace(/_/g, " ")}</span>;
}

export function HighRisk({ children = "HIGH RISK" }: { children?: React.ReactNode }) {
  return <span className="inline-block text-[10px] font-bold tracking-[0.12em] uppercase px-1.5 py-0.5 border border-accent-down text-accent-down">{children}</span>;
}

export function Button({ children, onClick, disabled, kind = "primary", type = "button" }: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean; kind?: "primary" | "ghost"; type?: "button" | "submit";
}) {
  const cls = kind === "primary" ? "btn-primary" : "text-sm px-3 py-2 border border-base-border text-base-text hover:bg-base-tint disabled:opacity-50";
  return <button type={type} className={cls} onClick={onClick} disabled={disabled}>{children}</button>;
}

export function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="grid gap-1 min-w-0">
      <span className="stat-label">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-base-muted">{hint}</span>}
    </label>
  );
}

export const inputCls = "h-9 bg-base-bg/80 border border-base-border px-2 text-sm text-base-text w-full font-mono-nums";

export function KeyValues({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1.5 text-sm">
      {items.map(([k, v]) => (
        <React.Fragment key={k}>
          <dt className="text-base-muted">{k}</dt>
          <dd className="font-mono-nums text-right">{v}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}
