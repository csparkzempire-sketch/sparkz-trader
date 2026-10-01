import { useEffect, useState } from "react";
import { EquityChart, Lines } from "../charts/Charts";
import { label, money, num, pct, signedMoney, tone } from "../components/format";
import { Button, Empty, ErrorBox, Field, HighRisk, inputCls, KeyValues, Page, PageHeader, Panel, StatCard, StatGrid, Table } from "../components/ui";
import { Dict, get, runJob } from "../services/api";

export type Kind = "run" | "stress" | "walk-forward" | "lab";
const TITLES: Record<Kind, [string, string]> = {
  run: ["Backtest", "Replays closed candles through the same robot, risk manager and simulated executor used for paper trading."],
  stress: ["Stress tests", "Synthetic market scenarios, execution-cost stress and the critical failure scenario (a strong move against an open basket)."],
  "walk-forward": ["Walk-forward", "Chronological train → validation → test folds. Only test results are out-of-sample."],
  lab: ["Parameter lab", "A small, hand-picked parameter set (max 12 combinations), ranked on validation and checked once on an untouched test period."],
};
const SCENARIOS = ["normal", "trend_up", "trend_down", "sideways", "high_vol", "spike", "reversals", "news_spikes", "adverse_trend"];

function Metrics({ m }: { m: Dict }) {
  return (
    <StatGrid cols={4}>
      <StatCard label="Net P&L" value={signedMoney(m.net_pnl)} tone={tone(m.net_pnl)} hint={pct(m.return_pct, 2, true)} />
      <StatCard label="Baskets" value={String(m.baskets)} hint={`win rate ${pct(m.win_rate_pct, 1)}`} />
      <StatCard label="Profit factor" value={num(m.profit_factor)} hint={`expectancy ${signedMoney(m.expectancy_per_basket)}`} />
      <StatCard label="Max drawdown" value={pct(m.max_drawdown_pct)} tone="down" hint={money(m.max_drawdown_usd)} />
      <StatCard label="Average win / loss" value={`${signedMoney(m.avg_basket_profit)} / ${signedMoney(m.avg_basket_loss)}`} />
      <StatCard label="Largest basket loss" value={signedMoney(m.largest_basket_loss)} tone="down" hint={m.mae_mfe?.largest_loss_in_avg_wins ? `= ${num(m.mae_mfe.largest_loss_in_avg_wins, 1)} average wins` : undefined} />
      <StatCard label="Worst floating P&L" value={signedMoney(m.worst_floating_pnl)} tone="down" hint={`worst MAE ${signedMoney(m.mae_mfe?.worst_mae)}`} />
      <StatCard label="Losing streak" value={String(m.max_consecutive_losses)} />
      <StatCard label="Max positions" value={String(m.max_positions_in_basket)} hint={`avg ${num(m.avg_positions_per_basket, 1)}`} />
      <StatCard label="Max exposure" value={money(m.max_notional_usd, 0)} hint={`${num(m.max_effective_leverage, 2)}x equity`} />
      <StatCard label="Max margin use" value={pct(m.max_margin_usage_pct)} hint={money(m.max_margin_usd)} />
      <StatCard label="Costs paid" value={money(m.costs?.total_usd)} hint={`spread ${money(m.costs?.spread_usd)} · slippage ${money(m.costs?.slippage_usd)}`} />
      <StatCard label="Buy & hold (same period)" value={pct(m.buy_and_hold?.price_change_pct, 2, true)} />
      <StatCard label="Time in market" value={pct(m.time_in_market_pct, 0)} />
      <StatCard label="Avg basket duration" value={`${num(m.avg_basket_minutes / 60, 1)} h`} />
      <StatCard label="Winners that sat through > 3x their profit" value={pct(m.mae_mfe?.winners_with_mae_over_3x_profit_pct, 0)} />
    </StatGrid>
  );
}

function Groups({ g, title }: { g: Dict; title: string }) {
  return (
    <Panel title={title}>
      <Table head={["", "Baskets", "Win %", "Net P&L", "PF", "Worst", "Avg MAE", "Avg pos"]} align={["l", "r", "r", "r", "r", "r", "r", "r"]}
        rows={Object.entries(g ?? {}).map(([k, v]: [string, any]) => [label(k), v.baskets, pct(v.win_rate_pct, 0), signedMoney(v.net_pnl), num(v.profit_factor), signedMoney(v.worst_basket), signedMoney(v.avg_mae), num(v.avg_positions, 1)])} />
    </Panel>
  );
}

function BacktestResult({ r }: { r: Dict }) {
  const m = r.metrics;
  const [all, setAll] = useState(false);
  const rows = r.baskets.slice().reverse().slice(0, all ? 500 : 50);
  return (
    <>
      <Panel title="Result" right={<span className="text-xs text-base-muted">{r.data_label ?? r.label} · {m.start?.slice(0, 10)} → {m.end?.slice(0, 10)}</span>}>
        <p className="text-sm mb-4">Over this period, under the assumptions below, this configuration {m.net_pnl >= 0 ? "made" : "lost"} <b>{signedMoney(m.net_pnl)}</b> in simulation. This is not a forecast.</p>
        <Metrics m={m} />
      </Panel>
      <Panel title="Equity"><EquityChart points={r.equity_curve} initial={m.starting_balance} height={260} /></Panel>
      <div className="grid xl:grid-cols-2 gap-5">
        <Groups g={m.by_regime} title="By market regime at entry" />
        <Groups g={m.by_close_reason} title="By close reason" />
      </div>
      <Panel title="Assumptions"><ul className="text-sm list-disc pl-5 space-y-1">{r.assumptions.map((a: string) => <li key={a}>{a}</li>)}</ul></Panel>
      <Panel title={`Baskets (latest ${rows.length} of ${m.baskets})`} right={r.baskets.length > 50 ? <button className="text-xs underline" onClick={() => setAll(!all)}>{all ? "show fewer" : "show more"}</button> : undefined}>
        <Table head={["Basket", "Dir", "Pos", "Lots", "P&L", "MAE", "Regime", "Reason", "Bars"]} align={["l", "l", "r", "r", "r", "r", "l", "l", "r"]}
          rows={rows.map((b: Dict) => [b.uid, b.direction, b.positions, num(b.max_lots, 2),
            <span className={b.pnl < 0 ? "text-accent-down" : "text-accent-up"}>{signedMoney(b.pnl)}</span>, signedMoney(b.mae), label(b.regime), label(b.close_reason), b.bars_held])} />
      </Panel>
    </>
  );
}

function StressResult({ r }: { r: Dict }) {
  const cf = r.critical_failure;
  const variants = Object.entries(cf.variants) as [string, Dict][];
  const [sel, setSel] = useState(variants[0][0]);
  const path = (cf.variants[sel].path as Dict[]).filter((p) => p.open !== false);
  return (
    <>
      <Panel title="Market scenarios (synthetic data, 3 seeds each)">
        <Table head={["Scenario", "Mean P&L", "Worst run", "Worst basket", "Worst DD", "Full grids", "Loss-limit closes", "Halts"]} align={["l", "r", "r", "r", "r", "r", "r", "r"]}
          rows={Object.entries(r.market_scenarios).map(([k, v]: [string, any]) => [<span title={v.description}>{label(k)}</span>,
            <span className={v.mean_net_pnl < 0 ? "text-accent-down" : ""}>{signedMoney(v.mean_net_pnl)}</span>, signedMoney(v.worst_net_pnl),
            signedMoney(v.worst_basket), pct(v.worst_drawdown_pct), v.baskets_at_max_positions, v.loss_limit_closes, v.halted_runs])} />
      </Panel>
      <Panel title="Execution stress">
        <Table head={["Variant", "Net P&L", "Baskets", "PF", "Max DD", "Costs"]} align={["l", "r", "r", "r", "r", "r"]}
          rows={Object.entries(r.execution_stress.variants).map(([k, v]: [string, any]) => [label(k), signedMoney(v.net_pnl), v.baskets, num(v.profit_factor), pct(v.max_drawdown_pct), money(v.costs_usd)])} />
      </Panel>
      <Panel title="Critical failure scenario" right={<span className="text-xs text-base-muted">synthetic</span>}>
        <p className="text-sm mb-3">{cf.description}</p>
        <Table head={["Variant", "Outcome", "Realized", "Worst floating", "Max pos", "Max exposure", "Max margin %", "Break-even distance", "Account DD"]}
          align={["l", "l", "r", "r", "r", "r", "r", "r", "r"]}
          rows={variants.map(([k, v]) => [<button className={`underline-offset-2 ${k === sel ? "underline" : ""}`} onClick={() => setSel(k)}>{label(k)}{k.includes("HIGH_RISK") && <> <HighRisk /></>}</button>,
            label(v.outcome), signedMoney(v.realized_pnl), signedMoney(v.worst_floating_pnl), v.max_positions, money(v.max_exposure_usd, 0),
            pct(v.max_margin_usage_pct), `${num(v.max_distance_to_break_even_atr, 1)} ATR`, pct(v.worst_account_drawdown_pct)])} />
        <div className="grid xl:grid-cols-2 gap-5 mt-5">
          <div><div className="stat-label mb-1">{label(sel)}: floating P&L and MAE by bar</div>
            <Lines data={path} x="bar" fmt={(v) => money(v, 0)} series={[{ key: "floating_pnl", color: "rgb(255 99 99)", name: "floating P&L" }, { key: "mae", color: "rgb(214 160 90)", name: "MAE" }]} /></div>
          <div><div className="stat-label mb-1">Exposure (USD) by bar</div>
            <Lines data={path} x="bar" fmt={(v) => money(v, 0)} series={[{ key: "exposure_usd", color: "rgb(120 190 210)", name: "exposure" }, { key: "margin_usd", color: "rgb(240 234 232)", name: "margin" }]} /></div>
        </div>
      </Panel>
    </>
  );
}

function WalkForwardResult({ r }: { r: Dict }) {
  const s = r.summary;
  return (
    <>
      <Panel title="Summary">
        <StatGrid cols={4}>
          <StatCard label="Avg return: train" value={pct(s.avg_return_pct.train, 2, true)} hint="in-sample, chosen" />
          <StatCard label="Avg return: validation" value={pct(s.avg_return_pct.validation, 2, true)} hint="used to choose" />
          <StatCard label="Avg return: TEST" value={pct(s.avg_return_pct.test, 2, true)} tone={tone(s.avg_return_pct.test)} hint="out-of-sample" />
          <StatCard label="Profitable test folds" value={`${s.test_folds_profitable} / ${s.folds}`} />
        </StatGrid>
        <p className="text-sm text-base-muted mt-3">{s.note}</p>
      </Panel>
      <Panel title="Folds">
        <Table head={["Fold", "Test period", "Chosen", "Train", "Validation", "Test", "Test DD", "Test baskets", "Buy & hold"]} align={["l", "l", "l", "r", "r", "r", "r", "r", "r"]}
          rows={r.folds.map((f: Dict) => [f.fold, `${f.test_period[0].slice(0, 10)} → ${f.test_period[1].slice(0, 10)}`,
            <span className="text-xs">{Object.entries(f.chosen).map(([k, v]) => `${k}=${v}`).join(", ") || "as configured"}</span>,
            pct(f.chosen_train.return_pct, 2, true), pct(f.chosen_validation.return_pct, 2, true),
            <b className={f.test.return_pct < 0 ? "text-accent-down" : "text-accent-up"}>{pct(f.test.return_pct, 2, true)}</b>,
            pct(f.test.max_drawdown_pct), f.test.baskets, pct(f.test.buy_and_hold_pct, 2, true)])} />
      </Panel>
    </>
  );
}

function LabResult({ r }: { r: Dict }) {
  return (
    <>
      <Panel title="Chosen on validation, checked once on test">
        <KeyValues items={[["Chosen", Object.entries(r.chosen).map(([k, v]) => `${k}=${v}`).join(", ")], ["Candidates tried", r.candidates_tried],
          ["Train/validation rank agreement", num(r.train_validation_rank_correlation)], ["Test period", `${r.split.test[0].slice(0, 10)} → ${r.split.test[1].slice(0, 10)}`],
          ["Test return", pct(r.test.return_pct, 2, true)], ["Test max drawdown", pct(r.test.max_drawdown_pct)], ["Test baskets", r.test.baskets]]} />
        {r.warnings.map((w: string) => <p key={w} className="text-sm text-accent-warn mt-2">{w}</p>)}
      </Panel>
      <Panel title="Every candidate (none hidden)">
        <Table head={["Parameters", "Train return", "Train DD", "Validation return", "Validation DD", "Val. baskets"]} align={["l", "r", "r", "r", "r", "r"]}
          rows={r.candidates.map((c: Dict) => [Object.entries(c.params).map(([k, v]) => `${k}=${v}`).join(", "), pct(c.train.return_pct, 2, true), pct(c.train.max_drawdown_pct),
            pct(c.validation.return_pct, 2, true), pct(c.validation.max_drawdown_pct), c.validation.baskets])} />
      </Panel>
    </>
  );
}

export default function ResearchPage({ kind }: { kind: Kind }) {
  const [presets, setPresets] = useState<Dict[]>([]);
  const [preset, setPreset] = useState("");
  const [data, setData] = useState("stored");
  const [order, setOrder] = useState("FAVOURABLE_FIRST");
  const [path, setPath] = useState("");
  const [space, setSpace] = useState(kind === "lab" ? "grid.atr_multiplier=0.5,1.0\ntarget.fixed_usd=5,10,20" : "grid.atr_multiplier=0.5,1.0");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<Dict | null>(null);
  useEffect(() => { get("/api/system/config").then((c) => setPresets(c.presets)).catch(() => {}); }, []);
  useEffect(() => { setRes(null); setErr(null); }, [kind]);
  const parseSpace = () => Object.fromEntries(space.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const [k, v] = l.split("=");
    return [k.trim(), v.split(",").map((x) => (isNaN(Number(x)) ? x.trim() : Number(x)))];
  }));
  const run = async () => {
    setErr(null); setRes(null); setBusy("starting");
    try {
      const body: Dict = { preset: preset || null, data, overrides: { execution: { intrabar_order: order } } };
      if (path) body.path = path;
      if (kind === "walk-forward" || kind === "lab") body.space = parseSpace();
      setRes(await runJob(kind, body, setBusy));
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(null);
    }
  };
  const p = presets.find((x) => x.name === preset);
  const [title, sub] = TITLES[kind];
  return (
    <>
      <PageHeader title={title} subtitle={sub} />
      <Page>
        <Panel title="Setup">
          <div className="grid md:grid-cols-4 gap-4">
            <Field label="Preset">
              <select className={inputCls} value={preset} onChange={(e) => setPreset(e.target.value)}>
                <option value="">default</option>
                {presets.map((x) => <option key={x.name} value={x.name}>{x.name}{x.high_risk.length ? " (HIGH RISK)" : ""}</option>)}
              </select>
            </Field>
            <Field label="Data" hint="stored = downloaded candles; synthetic data is labelled as such">
              <select className={inputCls} value={data} onChange={(e) => setData(e.target.value)}>
                <option value="stored">stored history</option>
                {SCENARIOS.map((s) => <option key={s} value={`synthetic:${s}`}>synthetic: {s}</option>)}
              </select>
            </Field>
            <Field label="Intrabar order" hint="ADVERSE_FIRST flatters grids">
              <select className={inputCls} value={order} onChange={(e) => setOrder(e.target.value)}>
                {["FAVOURABLE_FIRST", "RANDOM", "ADVERSE_FIRST"].map((o) => <option key={o}>{o}</option>)}
              </select>
            </Field>
            {kind === "run" && (
              <Field label="Intrabar path" hint="finer stored candles, where available">
                <select className={inputCls} value={path} onChange={(e) => setPath(e.target.value)}>
                  <option value="">synthetic OHLC path</option><option value="5m">5m candles</option><option value="1m">1m candles</option>
                </select>
              </Field>
            )}
          </div>
          {p && p.high_risk.length > 0 && <p className="text-sm text-accent-down mt-3"><HighRisk /> {p.high_risk.join("; ")}</p>}
          {(kind === "walk-forward" || kind === "lab") && (
            <div className="mt-4"><Field label="Parameter space (one per line: key=v1,v2)" hint="at most 12 combinations">
              <textarea className={`${inputCls} h-20 py-1`} value={space} onChange={(e) => setSpace(e.target.value)} />
            </Field></div>
          )}
          <div className="mt-4 flex items-center gap-3">
            <Button onClick={run} disabled={!!busy}>{busy ? `Running… ${busy}` : `Run ${title.toLowerCase()}`}</Button>
            <span className="text-xs text-base-muted">Simulation only. Results describe one period under stated assumptions.</span>
          </div>
        </Panel>
        <ErrorBox error={err} />
        {!res && !busy && !err && <Empty>No result yet.</Empty>}
        {res && kind === "run" && <BacktestResult r={res} />}
        {res && kind === "stress" && <StressResult r={res} />}
        {res && kind === "walk-forward" && <WalkForwardResult r={res} />}
        {res && kind === "lab" && <LabResult r={res} />}
      </Page>
    </>
  );
}
