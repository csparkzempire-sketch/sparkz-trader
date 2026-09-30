import { useState } from "react";
import { api } from "../services/api";
import type { Preset } from "../types/api";
import { Button, ErrorBox, Field, inputCls, Page, PageHeader, Panel, StatCard, StatGrid, Table } from "../components/ui";
import { SYMBOLS, TIMEFRAMES, useAppState, useLoad } from "../components/state";
import { label, money, num, pct, tone } from "../components/format";

type Kind = "stress" | "robustness" | "sensitivity" | "walk";

const Pnl = ({ v }: { v: any }) => <span className={tone(v) === "down" ? "text-accent-down" : tone(v) === "up" ? "text-accent-up" : ""}>{money(v)}</span>;

function Dist({ title, d, fmt }: { title: string; d: Record<string, number>; fmt: (v: number) => string }) {
  if (!d || !("p50" in d)) return null;
  return (
    <div className="panel p-3 min-w-0">
      <div className="stat-label">{title}</div>
      <div className="stat-value mt-1">{fmt(d.p50)}</div>
      <div className="text-[11px] text-base-muted mt-1">5–95%: {fmt(d.p5)} to {fmt(d.p95)} · worst {fmt(d.min)}</div>
    </div>
  );
}

export default function RobustnessPage() {
  const { symbol, setSymbol, timeframe, setTimeframe } = useAppState();
  const presets = useLoad<Preset[]>(() => api.presets(), []);
  const [preset, setPreset] = useState("B_atr_grid");
  const [busy, setBusy] = useState<Kind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [out, setOut] = useState<Partial<Record<Kind, any>>>({});

  async function go(kind: Kind) {
    setBusy(kind);
    setError(null);
    const body = { preset, overrides: { market: { symbol, timeframe } } };
    try {
      const r = kind === "stress" ? await api.stress(body) : kind === "robustness" ? await api.robustness({ ...body, runs: 40 })
        : kind === "sensitivity" ? await api.sensitivity(body) : await api.walkForward(body);
      setOut((o) => ({ ...o, [kind]: r }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  const st = out.stress, rb = out.robustness, se = out.sensitivity, wf = out.walk;
  return (
    <>
      <PageHeader title="Stress & Robustness" subtitle="How the strategy fails, how much of a result is luck, and how sensitive it is to its settings. Each study re-runs the full engine." />
      <Page>
        <ErrorBox error={error} />
        <Panel title="Study setup">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <Field label="Strategy"><select className={inputCls} value={preset} onChange={(e) => setPreset(e.target.value)}>{presets.data?.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}</select></Field>
            <Field label="Symbol"><select className={inputCls} value={symbol} onChange={(e) => setSymbol(e.target.value)}>{SYMBOLS.map((s) => <option key={s}>{s}</option>)}</select></Field>
            <Field label="Timeframe"><select className={inputCls} value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>{TIMEFRAMES.map((s) => <option key={s}>{s}</option>)}</select></Field>
          </div>
          <div className="flex flex-wrap gap-3 mt-4">
            <Button onClick={() => go("stress")} disabled={!!busy}>{busy === "stress" ? "Running…" : "Stress tests"}</Button>
            <Button onClick={() => go("robustness")} disabled={!!busy}>{busy === "robustness" ? "Running…" : "Monte Carlo (40 runs)"}</Button>
            <Button onClick={() => go("sensitivity")} disabled={!!busy}>{busy === "sensitivity" ? "Running…" : "Sensitivity"}</Button>
            <Button onClick={() => go("walk")} disabled={!!busy}>{busy === "walk" ? "Running…" : "Walk-forward & periods"}</Button>
          </div>
          <p className="text-xs text-base-muted mt-3">Studies take from a few seconds to a couple of minutes, depending on the history length.</p>
        </Panel>

        {st && (
          <Panel title="Stress scenarios">
            <Table
              head={["Scenario", "What it does", "Baskets", "Win rate", "Net P&L", "Max DD", "Largest loss", "At max positions", "Halted"]}
              align={["l", "l", "r", "r", "r", "r", "r", "r", "l"]}
              rows={st.rows.map((r: any) => [label(r.scenario), <span className="text-base-muted whitespace-normal">{r.description}</span>, r.baskets ?? "—",
                pct(r.win_rate_pct, 1), <Pnl v={r.net_pnl} />, pct(r.max_drawdown_pct), money(r.largest_basket_loss), r.baskets_at_max_positions ?? "—",
                r.halted ? <span className="text-accent-down">yes</span> : "no"])}
            />
            <div className="mt-5 grid md:grid-cols-2 gap-5">
              <div>
                <div className="stat-label mb-2">A full basket ({st.max_basket.planned_lots.length} positions)</div>
                <Table head={["Measure", "Value"]} align={["l", "r"]} rows={[
                  ["Lots per entry", st.max_basket.planned_lots.join(", ")],
                  ["Total lots", num(st.max_basket.total_lots)],
                  ["Notional", money(st.max_basket.notional_usd, 0)],
                  ["Margin", money(st.max_basket.margin_usd, 0)],
                  ["Leverage on starting equity", `${num(st.max_basket.leverage_at_start_equity, 1)}×`],
                  ["USD per 1 ATR move", money(st.max_basket.usd_per_1atr_move)],
                  ["Basket loss limit", money(st.max_basket.loss_limit_usd)],
                ]} />
              </div>
              <div>
                <div className="stat-label mb-2">Baskets that reached max positions (baseline)</div>
                <Table head={["Measure", "Value"]} align={["l", "r"]} rows={[
                  ["How many", st.max_basket.baskets_reaching_max],
                  ["Win rate", pct(st.max_basket.their_win_rate_pct, 1)],
                  ["Net P&L", money(st.max_basket.their_net_pnl)],
                  ["Average P&L", money(st.max_basket.their_avg_pnl)],
                ]} />
              </div>
            </div>
          </Panel>
        )}

        {rb && (
          <Panel title="Monte Carlo">
            <div className="stat-label mb-2">Basket order reshuffled / resampled ({rb.sequence.runs ?? 0} runs over {rb.sequence.baskets ?? 0} baskets)</div>
            {rb.sequence.note ? <p className="text-sm text-base-muted">{rb.sequence.note}</p> : (
              <StatGrid cols={4}>
                <StatCard label="Actual max drawdown" value={pct(rb.sequence.actual_max_drawdown_pct)} tone="down" />
                <Dist title="Drawdown, shuffled order" d={rb.sequence.permutation_max_drawdown_pct} fmt={(v) => pct(v)} />
                <Dist title="Return, resampled" d={rb.sequence.bootstrap_return_pct} fmt={(v) => pct(v, 2, true)} />
                <StatCard label="Chance of a loss / of hitting the DD limit" value={`${num(rb.sequence.prob_loss_pct, 0)}% / ${num(rb.sequence.prob_hit_drawdown_limit_pct, 0)}%`} />
              </StatGrid>
            )}
            <div className="stat-label mt-5 mb-2">Re-simulated with random costs, latency and parameters ({rb.perturbation.runs} runs)</div>
            <StatGrid cols={4}>
              <Dist title="Return" d={rb.perturbation.return_pct} fmt={(v) => pct(v, 2, true)} />
              <Dist title="Max drawdown" d={rb.perturbation.max_drawdown_pct} fmt={(v) => pct(v)} />
              <Dist title="Profit factor" d={rb.perturbation.profit_factor} fmt={(v) => num(v)} />
              <StatCard label="Profitable runs" value={pct(rb.perturbation.profitable_runs_pct, 0)} hint={`${num(rb.perturbation.halted_runs_pct, 0)}% of runs halted`} />
            </StatGrid>
          </Panel>
        )}

        {se && Object.entries(se).map(([k, rows]: [string, any]) => (
          <Panel key={k} title={`Sensitivity · ${label(k)}`}>
            <Table
              head={["Value", "Baskets", "Win rate", "Net P&L", "Profit factor", "Max DD", "Largest loss", "Avg positions", "Max leverage", "Halted"]}
              align={["l", "r", "r", "r", "r", "r", "r", "r", "r", "l"]}
              rows={rows.map((r: any) => [r.value, r.baskets, pct(r.win_rate_pct, 1), <Pnl v={r.net_pnl} />, num(r.profit_factor), pct(r.max_drawdown_pct),
                money(r.largest_basket_loss), num(r.avg_positions_per_basket, 2), `${num(r.max_effective_leverage, 1)}×`, r.halted ? <span className="text-accent-down">yes</span> : "no"])}
            />
          </Panel>
        ))}

        {wf && (
          <>
            <Panel title="Walk-forward (parameters chosen in-sample, judged out-of-sample)">
              <StatGrid cols={4}>
                <StatCard label="Out-of-sample net P&L" value={money(wf.walk_forward.out_of_sample.net_pnl)} tone={tone(wf.walk_forward.out_of_sample.net_pnl)} hint={`${wf.walk_forward.out_of_sample.baskets} baskets`} />
                <StatCard label="Profitable test windows" value={`${wf.walk_forward.out_of_sample.profitable_steps} of ${wf.walk_forward.out_of_sample.steps}`} />
                <StatCard label="Avg in-sample return" value={pct(wf.walk_forward.avg_in_sample_return_pct, 2, true)} />
                <StatCard label="Avg out-of-sample return" value={pct(wf.walk_forward.avg_out_of_sample_return_pct, 2, true)} tone={tone(wf.walk_forward.avg_out_of_sample_return_pct)} />
              </StatGrid>
              <div className="mt-4">
                <Table head={["Test window", "Chosen spacing", "Target %", "Max pos.", "In-sample return", "Out-of-sample return", "OOS baskets", "OOS max DD"]}
                  align={["l", "r", "r", "r", "r", "r", "r", "r"]}
                  rows={wf.walk_forward.steps.map((s: any) => [`${s.test[0].slice(0, 10)} → ${s.test[1].slice(0, 10)}`, num(s.chosen.spacing), num(s.chosen.target_percent),
                    s.chosen.max_positions, pct(s.in_sample?.return_pct, 2, true), pct(s.out_of_sample?.return_pct, 2, true), s.out_of_sample?.baskets ?? "—", pct(s.out_of_sample?.max_drawdown_pct)])} />
              </div>
            </Panel>
            <Panel title="Period by period (fixed settings, fresh capital each period)">
              <Table head={["Period", "Baskets", "Win rate", "Net P&L", "Profit factor", "Max DD", "Largest loss"]} align={["l", "r", "r", "r", "r", "r", "r"]}
                rows={wf.periods.map((p: any) => [p.period, p.baskets, pct(p.win_rate_pct, 1), <Pnl v={p.net_pnl} />, num(p.profit_factor), pct(p.max_drawdown_pct), money(p.largest_basket_loss)])} />
            </Panel>
          </>
        )}
      </Page>
    </>
  );
}
