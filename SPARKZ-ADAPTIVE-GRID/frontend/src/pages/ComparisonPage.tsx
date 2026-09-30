import { useState } from "react";
import { api } from "../services/api";
import type { CompareRow, Preset } from "../types/api";
import { Button, ErrorBox, Field, HighRisk, inputCls, Page, PageHeader, Panel, Table } from "../components/ui";
import { SYMBOLS, TIMEFRAMES, useAppState, useLoad } from "../components/state";
import { money, num, pct, tone } from "../components/format";
import { MultiEquity } from "../charts/Charts";
import { RegimeTable } from "./PerformancePage";

export default function ComparisonPage() {
  const { symbol, setSymbol, timeframe, setTimeframe } = useAppState();
  const presets = useLoad<Preset[]>(() => api.presets(), []);
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [spread, setSpread] = useState(1);
  const [result, setResult] = useState<{ rows: CompareRow[]; note: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [regimeKey, setRegimeKey] = useState<string | null>(null);
  const keys = chosen ?? presets.data?.map((p) => p.key) ?? [];

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.compare(keys, { market: { symbol, timeframe }, execution: { spread_multiplier: spread } });
      setResult(r);
      setRegimeKey(r.rows[0]?.key ?? null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const toggle = (k: string) => setChosen(keys.includes(k) ? keys.filter((x) => x !== k) : [...keys, k]);
  const rows = result?.rows.filter((r) => !r.error) ?? [];
  const regimeRow = rows.find((r) => r.key === regimeKey);

  return (
    <>
      <PageHeader title="Comparison Lab" subtitle="Each strategy runs separately on the same data, costs and risk limits. Nothing is combined, and nothing is ranked: the table is the measurement." />
      <Page>
        <ErrorBox error={error || presets.error} />
        <Panel title="Strategies">
          <div className="grid gap-4">
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
              {presets.data?.map((p) => (
                <label key={p.key} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={keys.includes(p.key)} onChange={() => toggle(p.key)} />
                  <span>{p.name}</span>
                  {p.config.sizing.mode === "MARTINGALE" && <HighRisk />}
                </label>
              ))}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 items-end">
              <Field label="Symbol"><select className={inputCls} value={symbol} onChange={(e) => setSymbol(e.target.value)}>{SYMBOLS.map((s) => <option key={s}>{s}</option>)}</select></Field>
              <Field label="Timeframe"><select className={inputCls} value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>{TIMEFRAMES.map((s) => <option key={s}>{s}</option>)}</select></Field>
              <Field label="Spread ×"><input className={inputCls} type="number" step="0.5" value={spread} onChange={(e) => setSpread(Number(e.target.value))} /></Field>
              <Button onClick={run} disabled={busy || keys.length === 0}>{busy ? "Running…" : `Compare ${keys.length} strategies`}</Button>
            </div>
          </div>
        </Panel>

        {result && (
          <>
            <Panel title="Side by side">
              <Table
                head={["Strategy", "Baskets", "Win rate", "Net P&L", "Profit factor", "Expectancy", "Max DD", "Largest loss", "Avg win", "Winners with MAE > profit", "Median profit ÷ MAE", "Avg positions", "At max", "Max leverage", "Halted"]}
                align={["l", "r", "r", "r", "r", "r", "r", "r", "r", "r", "r", "r", "r", "r", "l"]}
                rows={rows.map((r) => [
                  <span className="flex items-center gap-2">{r.name}{r.high_risk && <HighRisk />}</span>,
                  r.baskets, pct(r.win_rate_pct, 1),
                  <span className={tone(r.net_pnl) === "down" ? "text-accent-down" : "text-accent-up"}>{money(r.net_pnl)}</span>,
                  num(r.profit_factor), money(r.expectancy), pct(r.max_drawdown_pct), money(r.largest_basket_loss), money(r.avg_basket_profit),
                  pct(r.winners_with_mae_over_1x_profit_pct, 0), num(r.median_winner_profit_to_mae), num(r.avg_positions_per_basket, 2),
                  r.baskets_at_max_positions, `${num(r.max_effective_leverage, 1)}×`, r.halted ? <span className="text-accent-down">yes</span> : "no",
                ])}
              />
              <p className="text-xs text-base-muted mt-3">{result.note}</p>
            </Panel>
            <Panel title="Equity, all strategies"><MultiEquity series={rows.map((r) => ({ name: r.name, curve: r.curve }))} /></Panel>
            <Panel title="By market regime" right={
              <select className={inputCls + " w-auto"} value={regimeKey ?? ""} onChange={(e) => setRegimeKey(e.target.value)}>
                {rows.map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}
              </select>}>
              {regimeRow && <RegimeTable groups={regimeRow.by_regime} />}
            </Panel>
          </>
        )}
      </Page>
    </>
  );
}
