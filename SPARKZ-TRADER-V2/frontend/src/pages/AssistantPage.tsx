import { useCallback, useEffect, useState } from "react";
import { num, price } from "../components/format";
import { Button, Direction, Empty, ErrorBox, KeyValues, Page, PageHeader, Panel, StatCard, StatGrid, Table } from "../components/ui";
import { Dict, get } from "../services/api";

const LABELS: Record<string, string> = {
  trend_following: "Trend following", market_structure: "Market structure (BOS / CHoCH)",
  support_resistance: "Support & resistance", supply_demand: "Supply & demand", trendline_break: "Trendline break",
  sr_flip: "Support/resistance flip", range_trading: "Range trading", liquidity_sweep: "Liquidity sweep",
  scalping_momentum: "Scalping momentum",
};
const label = (k: string) => LABELS[k] ?? k;

/** Polls /api/assistant (the server refreshes the analysis at most every assistant.refresh_seconds). */
export function useAssistant(every = 30_000) {
  const [data, setData] = useState<Dict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setData(await get("/api/assistant")); setError(null); } catch (e) { setError((e as Error).message); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, every); return () => clearInterval(t); }, [load, every]);
  return { data, error, reload: load };
}

function minutes(m: unknown): string {
  if (typeof m !== "number") return "—";
  return m >= 1440 ? `${(m / 1440).toFixed(1)} d` : m >= 60 ? `${(m / 60).toFixed(1)} h` : `${m} min`;
}

export function Decision({ r, compact = false }: { r: Dict; compact?: boolean }) {
  const tone = r.action === "BUY" ? "up" : r.action === "SELL" ? "down" : "neutral";
  return (
    <div className="grid gap-3">
      <StatGrid cols={compact ? 2 : 4}>
        <StatCard label="Assistant decision" value={r.action} tone={tone} hint={`needs ${num(r.min_confidence_pct, 0)}% confidence`} />
        <StatCard label="Confidence" value={`${num(r.confidence_pct, 1)}%`}
          hint={`buy votes ${num(r.direction_votes?.buy, 2)} · sell votes ${num(r.direction_votes?.sell, 2)}`} />
        {!compact && <StatCard label="Higher-timeframe bias" value={r.higher_timeframe_bias} hint="1d · 4h · 1h trend vote" />}
        {!compact && <StatCard label="Price" value={price(r.price)} hint={`spread used ${num(r.spread, 2)}`} />}
      </StatGrid>
      <ul className="text-sm list-disc pl-5 grid gap-1">
        {(r.reasons ?? []).map((x: string, i: number) => <li key={i}>{x}</li>)}
      </ul>
    </div>
  );
}

function Plan({ p }: { p: Dict | null }) {
  if (!p) return <Panel title="Trade plan"><Empty>No setup near the execution timeframe right now.</Empty></Panel>;
  const h = p.history ?? {};
  return (
    <Panel title="Trade plan" right={<span className="text-xs text-base-muted">{label(p.strategy)} · {p.tf}</span>}>
      <div className="grid md:grid-cols-2 gap-x-8 gap-y-4">
        <KeyValues items={[
          ["Direction", <Direction d={p.action} />],
          ["Entry", price(p.entry)],
          ["Stop", price(p.stop)],
          ["Target", price(p.target)],
          ["Reward : risk", `${num(p.risk_reward, 2)} : 1`],
          ["Why", (p.reasons ?? []).join("; ")],
        ]} />
        <KeyValues items={[
          ["Past setups of this kind", h.samples ?? 0],
          ["Target reached first", h.samples ? `${num(h.hit_rate_pct, 1)}%` : "—"],
          ["Average result (after spread)", h.samples ? `${num(h.avg_r_net, 2)} R` : "—"],
          ["Time to play out: fast (25%)", minutes(h.minutes?.fast_p25)],
          ["Typical (median)", minutes(h.minutes?.typical_p50)],
          ["Slow (75%)", minutes(h.minutes?.slow_p75)],
        ]} />
      </div>
      <p className="text-[11px] text-base-muted mt-3">Times and hit rate come from this detector's earlier setups on the same timeframe, measured on candles before now.</p>
    </Panel>
  );
}

function FitBars({ fit }: { fit: Dict }) {
  const rows = Object.entries(fit ?? {}) as [string, number][];
  return (
    <Panel title="Which strategies fit the market now" right={<span className="text-xs text-base-muted">share of total fit, all timeframes</span>}>
      <div className="grid gap-2">
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[11rem_minmax(0,1fr)_3.5rem] items-center gap-3 text-sm">
            <span className="truncate">{label(k)}</span>
            <div className="h-2.5 bg-base-tint"><div className="h-2.5 bg-accent-brand" style={{ width: `${Math.min(100, v)}%` }} /></div>
            <span className="text-right font-mono-nums">{num(v, 1)}%</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function Timeframes({ tfs }: { tfs: Dict }) {
  const rows = Object.entries(tfs ?? {}).map(([tf, v]: [string, any]) => [
    tf, v.trend, num(v.adx, 0), num(v.rsi, 0), v.last_structure_event ?? "—",
    (v.signals ?? []).length ? (v.signals as Dict[]).map((s) => `${s.action} ${label(s.strategy)}${s.age_candles ? ` (${s.age_candles} ago)` : ""}`).join(" · ") : "—",
  ]);
  return <Panel title="Timeframe by timeframe"><Table head={["TF", "Trend", "ADX", "RSI", "Last structure", "Active setups"]} rows={rows} /></Panel>;
}

export default function AssistantPage() {
  const { data, error, reload } = useAssistant();
  const r = data?.report;
  const mode = data?.drives_entries ? "decides the robot's entries (entry mode ASSISTANT)"
    : data?.gate_entries ? "must agree before the robot opens a trade" : "advisory only (gate off)";
  return (
    <Page>
      <PageHeader title="Market assistant" subtitle={`Reads 1d, 4h, 1h, 15m, 5m and 1m candles with nine strategies before any trade. The assistant ${mode}.`}
        right={<Button kind="ghost" onClick={reload}>Refresh</Button>} />
      <ErrorBox error={error || data?.error} />
      {!r ? <Empty>Waiting for enough candles to analyse…</Empty> : (
        <>
          <Panel title="Decision" right={<span className="text-xs text-base-muted">timeframes: {(r.timeframes_available ?? []).join(" · ")} · updated {num(data?.age_seconds, 0)} s ago</span>}>
            <Decision r={r} />
          </Panel>
          <Plan p={r.plan} />
          <FitBars fit={r.strategy_fit_pct} />
          <Timeframes tfs={r.timeframes} />
          <p className="text-[11px] text-base-muted">{r.disclaimer}</p>
        </>
      )}
    </Page>
  );
}
