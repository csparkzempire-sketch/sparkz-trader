import { useState } from "react";
import { EquityChart } from "../charts/Charts";
import { num, signedMoney, tone, when } from "../components/format";
import { Button, Empty, ErrorBox, Field, Page, PageHeader, Panel, StatCard, StatGrid, Table } from "../components/ui";
import { Dict, get, post } from "../services/api";

const NAMES: Record<string, string> = {
  trend_following: "Trend following", market_structure: "Market structure", support_resistance: "Support & resistance",
  supply_demand: "Supply & demand", trendline_break: "Trendline break", sr_flip: "S/R flip", range_trading: "Range trading",
  liquidity_sweep: "Liquidity sweep", scalping_momentum: "Scalping momentum",
};
const input = "bg-base-panel border border-base-border px-3 py-2 text-sm w-full";

export default function AssistantBacktestPage() {
  const [f, setF] = useState({ execution_tf: "1d", start: "2008-01-01", spread: 0.24, min_confidence: 0.55, decision: "VOTE", top: 1, download: true });
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [r, setR] = useState<Dict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [allTrades, setAllTrades] = useState(false);
  const set = (k: string, v: unknown) => setF({ ...f, [k]: v });

  const run = async () => {
    setBusy(true); setError(null); setR(null); setProgress("starting");
    try {
      const { job } = await post<{ job: string }>("/api/backtest/assistant", f);
      for (;;) {
        await new Promise((res) => setTimeout(res, 1500));
        const j = await get(`/api/backtest/jobs/${job}`);
        if (j.progress) setProgress(j.progress);
        if (j.status === "done") { setR(j.result); break; }
        if (j.status === "error") throw new Error(j.error);
      }
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const o: Dict = r?.overall ?? {};
  const years: [string, Dict][] = Object.entries(r?.by_year ?? {});
  const posYears = years.filter(([, v]) => v.net_usd > 0).length;
  return (
    <>
      <PageHeader title="Assistant backtest" subtitle="Replays the assistant robot (the one your demo runs) over hourly gold history since 2008, deciding only on candles that had closed, and trades every BUY/SELL at your spread." />
      <Page>
        <Panel title="Setup">
          <div className="grid gap-3 md:grid-cols-4">
            <Field label="Robot timeframe" hint="your demo runs 1d">
              <select className={input} value={f.execution_tf} onChange={(e) => set("execution_tf", e.target.value)}>
                <option value="1d">1d (daily)</option><option value="4h">4h</option><option value="1h">1h</option>
              </select>
            </Field>
            <Field label="From" hint="the first year is warm-up">
              <input className={input} type="date" value={f.start} min="2008-01-01" onChange={(e) => set("start", e.target.value)} />
            </Field>
            <Field label="Spread (USD)" hint="your Exness gold: about 0.22-0.24">
              <input className={input} type="number" step="0.01" min="0" value={f.spread} onChange={(e) => set("spread", Number(e.target.value))} />
            </Field>
            <Field label="Confidence needed" hint="0.55 = 55%, as live">
              <input className={input} type="number" step="0.05" min="0" max="1" value={f.min_confidence} onChange={(e) => set("min_confidence", Number(e.target.value))} />
            </Field>
            <Field label="Decision rule">
              <select className={input} value={f.decision} onChange={(e) => set("decision", e.target.value)}>
                <option value="VOTE">All strategies vote (live default)</option><option value="TOP_STRATEGY">Follow the top-ranked strategy</option>
              </select>
            </Field>
            {f.decision === "TOP_STRATEGY" && (
              <Field label="Look at the top">
                <select className={input} value={f.top} onChange={(e) => set("top", Number(e.target.value))}>
                  {[1, 2, 3, 5].map((n) => <option key={n} value={n}>{n} strateg{n === 1 ? "y" : "ies"}</option>)}
                </select>
              </Field>
            )}
            <Field label="History" hint="free Dukascopy hourly gold, saved on this PC">
              <label className="flex items-center gap-2 text-sm py-2"><input type="checkbox" checked={f.download} onChange={(e) => set("download", e.target.checked)} />download / update first</label>
            </Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button onClick={run} disabled={busy}>{busy ? "Running…" : "Run backtest"}</Button>
            {busy && <span className="text-sm text-base-muted">{progress} · the first download takes several minutes, a full run about 4 minutes</span>}
          </div>
        </Panel>
        {error && <ErrorBox error={error} />}
        {r && (o.trades ? (
          <>
            <StatGrid cols={4}>
              <StatCard label="Trades" value={String(o.trades)} hint={`${r.meta.first_decision?.slice(0, 10)} → ${r.meta.last_decision?.slice(0, 10)}`} />
              <StatCard label="Win rate" value={`${num(o.win_rate_pct, 1)}%`} hint="targets are 2 R, so under 50% can still pay" />
              <StatCard label="Average per trade" value={`${o.expectancy_r > 0 ? "+" : ""}${num(o.expectancy_r, 3)} R`} tone={tone(o.expectancy_r)} hint={`before costs ${num(o.gross_expectancy_r, 3)} R`} />
              <StatCard label="Reliability (t)" value={num(o.t_stat, 2)} tone={o.t_stat >= 2 ? "up" : "neutral"} hint={o.t_stat >= 2 ? "2 or more: unlikely to be luck" : "below 2: could be luck"} />
              <StatCard label="Net, risking $100 a trade" value={signedMoney(o.net_usd)} tone={tone(o.net_usd)} hint={`profit factor ${num(o.profit_factor, 2)}`} />
              <StatCard label="Worst drop" value={`${num(o.max_drawdown_pct, 1)}%`} tone="down" hint={`${o.max_consecutive_losses} losses in a row at most`} />
              <StatCard label="Positive years" value={`${posYears} / ${years.length}`} />
              <StatCard label="Exits" value={Object.entries(o.exit_reasons ?? {}).map(([k, v]) => `${k.toLowerCase()} ${v}`).slice(0, 3).join(" · ")} />
            </StatGrid>
            <Panel title="Result over time (risking $100 a trade)">
              <EquityChart points={r.equity_curve ?? []} initial={0} height={260} />
            </Panel>
            <div className="grid gap-5 lg:grid-cols-2">
              <Panel title="By year">
                <Table head={["Year", "Trades", "Avg R", "Net $"]} align={["l", "r", "r", "r"]}
                  rows={years.map(([y, v]) => [y, v.trades, num(v.expectancy_r, 3), <span className={v.net_usd >= 0 ? "text-accent-up" : "text-accent-down"}>{signedMoney(v.net_usd)}</span>])} />
              </Panel>
              <Panel title="By the strategy behind the trade">
                <Table head={["Strategy", "Trades", "Win %", "Avg R", "Net $"]} align={["l", "r", "r", "r", "r"]}
                  rows={Object.entries(r.by_strategy ?? {}).sort((a: any, b: any) => b[1].net_usd - a[1].net_usd).map(([k, v]: [string, any]) =>
                    [NAMES[k] ?? k, v.trades, num(v.win_rate_pct, 1), num(v.expectancy_r, 3), <span className={v.net_usd >= 0 ? "text-accent-up" : "text-accent-down"}>{signedMoney(v.net_usd)}</span>])} />
              </Panel>
            </div>
            <Panel title="By confidence">
              <Table head={["Confidence", "Trades", "Win %", "Avg R", "Net $"]} align={["l", "r", "r", "r", "r"]}
                rows={Object.entries(r.by_band ?? {}).map(([k, v]: [string, any]) => [`${k}%`, v.trades, num(v.win_rate_pct, 1), num(v.expectancy_r, 3), signedMoney(v.net_usd)])} />
            </Panel>
            <Panel title={`Latest ${allTrades ? r.trades?.length ?? 0 : Math.min(30, r.trades?.length ?? 0)} trades`}
              right={(r.trades?.length ?? 0) > 30 ? <button className="text-xs underline" onClick={() => setAllTrades(!allTrades)}>{allTrades ? "show fewer" : `show ${r.trades.length}`}</button> : undefined}>
              <Table head={["Opened", "Side", "Strategy", "Entry", "Stop", "Target", "Exit", "Why", "R"]} align={["l", "l", "l", "r", "r", "r", "r", "l", "r"]}
                rows={(r.trades ?? []).slice(0, allTrades ? undefined : 30).map((t: Dict) => [when(t.entry_time), <span className={t.side === "BUY" ? "text-accent-up" : "text-accent-down"}>{t.side}</span>,
                  `${NAMES[t.strategy] ?? t.strategy} (${t.tf})`, num(t.entry, 2), num(t.stop, 2), num(t.target, 2), num(t.exit, 2), String(t.exit_reason).toLowerCase(),
                  <span className={t.r > 0 ? "text-accent-up" : "text-accent-down"}>{num(t.r, 2)}</span>])} />
            </Panel>
            <Panel title="How this test works">
              <ul className="text-sm list-disc pl-5 space-y-1 text-base-muted">
                <li>{r.meta.exits}; spread {r.meta.spread} plus slippage; overnight swap is not charged.</li>
                <li>The assistant sees 1h, 4h and 1d candles built from the hourly history, only those closed at each decision.</li>
                <li>Results are in R (1 R = the trade's risk to its stop) and in dollars when every trade risks $100. Your demo risks less: 0.01 lot.</li>
                <li>A past result is not a promise. The technical report (sections 21 and 22) has the full study.</li>
              </ul>
            </Panel>
          </>
        ) : <Empty>No trades in this period.</Empty>)}
        {!r && !busy && !error && <p className="text-xs text-base-muted">Data: hourly gold bid/ask from Dukascopy (free), stored under data/candles on this computer.</p>}
      </Page>
    </>
  );
}
