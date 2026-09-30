import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import type { BacktestSummary, Preset } from "../types/api";
import { Button, ErrorBox, Field, HighRisk, inputCls, Page, PageHeader, Panel, StatCard, StatGrid, Table } from "../components/ui";
import { SYMBOLS, TIMEFRAMES, useAppState, useLoad } from "../components/state";
import { money, num, pct, tone, when } from "../components/format";

type Cfg = Record<string, any>;

function setPath(cfg: Cfg, path: string, value: unknown): Cfg {
  const [a, b] = path.split(".");
  return b ? { ...cfg, [a]: { ...cfg[a], [b]: value } } : { ...cfg, [a]: value };
}

const numberOr = (v: string, fallback: number | null = null) => (v === "" ? fallback : Number(v));

export default function BacktestPage() {
  const { symbol, timeframe, setBacktestId } = useAppState();
  const presets = useLoad<Preset[]>(() => api.presets(), []);
  const runs = useLoad(() => api.listBacktests(), []);
  const [presetKey, setPresetKey] = useState("B_atr_grid");
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [result, setResult] = useState<BacktestSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const p = presets.data?.find((x) => x.key === presetKey);
    if (p) setCfg({ ...p.config, market: { ...p.config.market, symbol, timeframe } });
  }, [presets.data, presetKey, symbol, timeframe]);

  const set = (path: string, v: unknown) => setCfg((c) => (c ? setPath(c, path, v) : c));

  async function run() {
    if (!cfg) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.runBacktest({ overrides: cfg, name: `${cfg.name} · ${cfg.market.symbol} ${cfg.market.timeframe}`, save: true });
      setResult(r);
      setBacktestId(r.id);
      runs.reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const martingale = cfg?.sizing?.mode === "MARTINGALE";
  const m = result?.metrics;

  return (
    <>
      <PageHeader title="Backtest" subtitle="Configure a strategy and run it over stored history. Signals fill on the next bar's open; spread, slippage and commission are charged on every fill." />
      <Page>
        <ErrorBox error={error || presets.error} />
        {cfg && (
          <Panel title="Configuration" right={<span className="text-xs text-base-muted">Starting point: preset below; every field can be changed</span>}>
            <div className="grid gap-5">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 items-start">
                <Field label="Strategy preset">
                  <select className={inputCls} value={presetKey} onChange={(e) => setPresetKey(e.target.value)}>
                    {presets.data?.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
                  </select>
                </Field>
                <Field label="Symbol">
                  <select className={inputCls} value={cfg.market.symbol} onChange={(e) => set("market.symbol", e.target.value)}>
                    {SYMBOLS.map((s) => <option key={s}>{s}</option>)}
                  </select>
                </Field>
                <Field label="Timeframe">
                  <select className={inputCls} value={cfg.market.timeframe} onChange={(e) => set("market.timeframe", e.target.value)}>
                    {TIMEFRAMES.map((s) => <option key={s}>{s}</option>)}
                  </select>
                </Field>
                <Field label="Initial capital">
                  <input className={inputCls} type="number" value={cfg.risk.initial_capital} onChange={(e) => set("risk.initial_capital", Number(e.target.value))} />
                </Field>
                <Field label="From (UTC date)" hint="Empty: all stored data">
                  <input className={inputCls} type="date" value={cfg.market.start?.slice(0, 10) ?? ""} onChange={(e) => set("market.start", e.target.value || null)} />
                </Field>
                <Field label="To (UTC date)">
                  <input className={inputCls} type="date" value={cfg.market.end?.slice(0, 10) ?? ""} onChange={(e) => set("market.end", e.target.value || null)} />
                </Field>
                <Field label="Entry mode">
                  <select className={inputCls} value={cfg.entry.mode} onChange={(e) => set("entry.mode", e.target.value)}>
                    <option value="TREND">Trend (EMA/RSI/ADX)</option>
                    <option value="RANGE_FADE">Range fade (Bollinger/RSI)</option>
                  </select>
                </Field>
                <Field label="Skip high volatility">
                  <select className={inputCls} value={String(cfg.entry.skip_high_volatility)} onChange={(e) => set("entry.skip_high_volatility", e.target.value === "true")}>
                    <option value="true">Yes</option><option value="false">No</option>
                  </select>
                </Field>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 items-start">
                <Field label="Grid method">
                  <select className={inputCls} value={cfg.grid.mode} onChange={(e) => set("grid.mode", e.target.value)}>
                    <option value="PRICE">A · Price distance</option>
                    <option value="ATR">B · ATR adaptive</option>
                    <option value="SIGNAL_CONFIRMED">C · Signal-confirmed</option>
                    <option value="NONE">None (single position)</option>
                  </select>
                </Field>
                {cfg.grid.mode === "PRICE" ? (
                  <>
                    <Field label="Grid step (price units)"><input className={inputCls} type="number" step="0.1" value={cfg.grid.price_step} onChange={(e) => set("grid.price_step", Number(e.target.value))} /></Field>
                    <Field label="Step growth per level" hint="e.g. 1.00 → 1, 2, 3"><input className={inputCls} type="number" step="0.1" value={cfg.grid.step_growth} onChange={(e) => set("grid.step_growth", Number(e.target.value))} /></Field>
                  </>
                ) : (
                  <Field label="Grid = ATR ×"><input className={inputCls} type="number" step="0.05" value={cfg.grid.atr_multiplier} onChange={(e) => set("grid.atr_multiplier", Number(e.target.value))} /></Field>
                )}
                <Field label="Max positions per basket" hint="Hard ceiling 20">
                  <input className={inputCls} type="number" min={1} max={20} value={cfg.risk.max_positions} onChange={(e) => set("risk.max_positions", Number(e.target.value))} />
                </Field>
                <Field label="Position sizing">
                  <select className={inputCls} value={cfg.sizing.mode} onChange={(e) => set("sizing.mode", e.target.value)}>
                    <option value="FIXED">Fixed (1,1,1…)</option>
                    <option value="LINEAR">Linear (1,2,3…)</option>
                    <option value="PYRAMID">Pyramiding (adds in profit)</option>
                    <option value="MARTINGALE">Martingale (1,2,4…) HIGH RISK</option>
                  </select>
                </Field>
                <Field label="Base lot size">
                  <select className={inputCls} value={cfg.sizing.base_lot_mode} onChange={(e) => set("sizing.base_lot_mode", e.target.value)}>
                    <option value="FIXED">Fixed lots</option>
                    <option value="ATR_NORMALIZED">Same $ risk per ATR</option>
                  </select>
                </Field>
                {cfg.sizing.base_lot_mode === "ATR_NORMALIZED" ? (
                  <Field label="USD per 1-ATR move" hint="Base lot set per basket"><input className={inputCls} type="number" step="1" value={cfg.sizing.usd_per_atr} onChange={(e) => set("sizing.usd_per_atr", Number(e.target.value))} /></Field>
                ) : (
                  <Field label="Base lot"><input className={inputCls} type="number" step="0.01" value={cfg.sizing.base_lot} onChange={(e) => set("sizing.base_lot", Number(e.target.value))} /></Field>
                )}
                {martingale && (
                  <Field label="Martingale" hint="Refused unless ticked">
                    <span className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={!!cfg.sizing.allow_martingale} onChange={(e) => set("sizing.allow_martingale", e.target.checked)} />
                      Allow <HighRisk />
                    </span>
                  </Field>
                )}
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 items-start">
                <Field label="Basket target">
                  <select className={inputCls} value={cfg.target.mode} onChange={(e) => set("target.mode", e.target.value)}>
                    <option value="FIXED">Fixed USD</option>
                    <option value="PERCENT">% of equity</option>
                    <option value="RISK_REWARD">Risk / reward</option>
                    <option value="ATR">ATR distance</option>
                  </select>
                </Field>
                {cfg.target.mode === "FIXED" && <Field label="Target (USD)"><input className={inputCls} type="number" value={cfg.target.fixed_usd} onChange={(e) => set("target.fixed_usd", Number(e.target.value))} /></Field>}
                {cfg.target.mode === "PERCENT" && <Field label="Target (% of equity)"><input className={inputCls} type="number" step="0.05" value={cfg.target.percent} onChange={(e) => set("target.percent", Number(e.target.value))} /></Field>}
                {cfg.target.mode === "RISK_REWARD" && <Field label="Target (× loss limit)"><input className={inputCls} type="number" step="0.1" value={cfg.target.risk_reward} onChange={(e) => set("target.risk_reward", Number(e.target.value))} /></Field>}
                {cfg.target.mode === "ATR" && <Field label="Target (ATR × beyond avg entry)"><input className={inputCls} type="number" step="0.1" value={cfg.target.atr_multiplier} onChange={(e) => set("target.atr_multiplier", Number(e.target.value))} /></Field>}
                <Field label="Basket loss limit (% equity)" hint="The tighter of this and risk per cycle">
                  <input className={inputCls} type="number" step="0.1" value={cfg.stop.max_basket_loss_percent} onChange={(e) => set("stop.max_basket_loss_percent", Number(e.target.value))} />
                </Field>
                <Field label="Risk per cycle (fraction)"><input className={inputCls} type="number" step="0.005" value={cfg.risk.risk_per_cycle} onChange={(e) => set("risk.risk_per_cycle", Number(e.target.value))} /></Field>
                <Field label="Time stop (bars)" hint="Empty: none"><input className={inputCls} type="number" value={cfg.stop.max_basket_bars ?? ""} onChange={(e) => set("stop.max_basket_bars", numberOr(e.target.value))} /></Field>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 items-start">
                <Field label="Max daily loss (%)"><input className={inputCls} type="number" step="0.5" value={cfg.risk.max_daily_loss_percent} onChange={(e) => set("risk.max_daily_loss_percent", Number(e.target.value))} /></Field>
                <Field label="Max account drawdown (%)" hint="Closes and halts"><input className={inputCls} type="number" step="0.5" value={cfg.risk.max_account_drawdown_percent} onChange={(e) => set("risk.max_account_drawdown_percent", Number(e.target.value))} /></Field>
                <Field label="Max leverage (× equity)"><input className={inputCls} type="number" value={cfg.risk.max_exposure_leverage} onChange={(e) => set("risk.max_exposure_leverage", Number(e.target.value))} /></Field>
                <Field label="Max margin use (%)"><input className={inputCls} type="number" value={cfg.risk.max_margin_usage_percent} onChange={(e) => set("risk.max_margin_usage_percent", Number(e.target.value))} /></Field>
                <Field label="Cooldown after loss (bars)"><input className={inputCls} type="number" value={cfg.risk.cooldown_bars_after_loss} onChange={(e) => set("risk.cooldown_bars_after_loss", Number(e.target.value))} /></Field>
                <Field label="Spread ×"><input className={inputCls} type="number" step="0.25" value={cfg.execution.spread_multiplier} onChange={(e) => set("execution.spread_multiplier", Number(e.target.value))} /></Field>
                <Field label="Slippage ×"><input className={inputCls} type="number" step="0.25" value={cfg.execution.slippage_multiplier} onChange={(e) => set("execution.slippage_multiplier", Number(e.target.value))} /></Field>
                <Field label="Commission (USD / lot / side)"><input className={inputCls} type="number" step="0.5" value={cfg.execution.commission_per_lot_side} onChange={(e) => set("execution.commission_per_lot_side", Number(e.target.value))} /></Field>
                <Field label="Entry latency (bars)"><input className={inputCls} type="number" min={0} value={cfg.execution.entry_latency_bars} onChange={(e) => set("execution.entry_latency_bars", Number(e.target.value))} /></Field>
                <Field label="Intrabar order" hint="Pessimistic: the worse of two paths">
                  <select className={inputCls} value={cfg.execution.intrabar_mode} onChange={(e) => set("execution.intrabar_mode", e.target.value)}>
                    <option value="PESSIMISTIC">Pessimistic</option>
                    <option value="OHLC_PATH">OHLC path</option>
                  </select>
                </Field>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <Button onClick={run} disabled={busy}>{busy ? "Running…" : "Run backtest"}</Button>
                {martingale && <HighRisk>Martingale sizing: exposure doubles at every level</HighRisk>}
              </div>
            </div>
          </Panel>
        )}

        {m && (
          <Panel title={`Result · ${result?.name ?? ""}`} right={<Link to="/performance" className="text-accent-brass text-sm font-semibold">Full performance →</Link>}>
            <StatGrid cols={6}>
              <StatCard label="Net P&L" value={money(m.net_pnl)} tone={tone(m.net_pnl)} hint={pct(m.return_pct, 2, true)} />
              <StatCard label="Baskets" value={String(m.baskets)} hint={`${num(m.win_rate_pct, 1)}% won`} />
              <StatCard label="Profit factor" value={num(m.profit_factor)} />
              <StatCard label="Max drawdown" value={pct(m.max_drawdown_pct)} tone="down" />
              <StatCard label="Largest basket loss" value={money(m.largest_basket_loss)} tone="down" hint={`avg win ${money(m.avg_basket_profit)}`} />
              <StatCard label="Baskets at max positions" value={String(m.baskets_at_max_positions)} hint={`avg ${num(m.avg_positions_per_basket, 1)} positions`} />
            </StatGrid>
            {m.halted && <p className="mt-3 text-sm text-accent-down">Halted: {m.halted}</p>}
          </Panel>
        )}

        <Panel title="Saved runs">
          <Table
            head={["#", "Name", "Market", "Period", "Baskets", "Net P&L", "Max DD", ""]}
            align={["l", "l", "l", "l", "r", "r", "r", "l"]}
            rows={(runs.data ?? []).map((r) => [
              r.id, r.name, `${r.symbol} ${r.timeframe}`, `${when(r.start)} – ${when(r.end)}`, r.baskets ?? "—",
              <span className={tone(r.net_pnl) === "down" ? "text-accent-down" : "text-accent-up"}>{money(r.net_pnl)}</span>,
              pct(r.max_drawdown_pct),
              <Link to="/performance" onClick={() => setBacktestId(r.id)} className="text-accent-brass font-semibold">Open</Link>,
            ])}
            empty="No saved runs yet."
          />
        </Panel>
      </Page>
    </>
  );
}
