import { useEffect, useState } from "react";
import { api } from "../services/api";
import type { PaperStatus, Preset } from "../types/api";
import { Button, Direction, ErrorBox, Field, inputCls, Page, PageHeader, Panel, StatCard, StatGrid, Table } from "../components/ui";
import { SYMBOLS, TIMEFRAMES, useAppState, useLoad } from "../components/state";
import { label, money, num, pct, signedMoney, tone, when } from "../components/format";
import { EquityChart } from "../charts/Charts";
import { BasketStatus } from "./BasketPage";

export default function PaperPage() {
  const { account, setAccount } = useAppState();
  const accounts = useLoad(() => api.paperAccounts(), []);
  const presets = useLoad<Preset[]>(() => api.presets(), []);
  const [status, setStatus] = useState<PaperStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ name: "gold-15m", preset: "B_atr_grid", symbol: "XAUUSD", timeframe: "15m" });

  const load = async (name: string) => {
    try {
      setStatus(await api.paperStatus(name));
      setError(null);
    } catch (e: any) {
      setError(e.message);
      setStatus(null);
    }
  };
  useEffect(() => {
    const first = account ?? accounts.data?.[0]?.name ?? null;
    if (first) load(first);
  }, [account, accounts.data]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const s = status;
  return (
    <>
      <PageHeader title="Paper Trading" subtitle="The backtest engine run forward on new closed candles with virtual money. Nothing here can reach a broker." />
      <Page>
        <ErrorBox error={error || accounts.error} />
        <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-5">
          <Panel title="Accounts">
            <Table head={["Account", "Market", "Strategy", "Equity", "Halted", ""]} align={["l", "l", "l", "r", "l", "l"]}
              rows={(accounts.data ?? []).map((a) => [a.name, `${a.symbol} ${a.timeframe}`, a.strategy, money(a.equity), a.halted ? <span className="text-accent-down">yes</span> : "no",
                <button className="text-accent-brass font-semibold" onClick={() => setAccount(a.name)}>View</button>])}
              empty="No paper accounts yet." />
          </Panel>
          <Panel title="New account">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Name"><input className={inputCls} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
              <Field label="Strategy"><select className={inputCls} value={form.preset} onChange={(e) => setForm({ ...form, preset: e.target.value })}>
                {presets.data?.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}</select></Field>
              <Field label="Symbol"><select className={inputCls} value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })}>{SYMBOLS.map((x) => <option key={x}>{x}</option>)}</select></Field>
              <Field label="Timeframe"><select className={inputCls} value={form.timeframe} onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>{TIMEFRAMES.map((x) => <option key={x}>{x}</option>)}</select></Field>
            </div>
            <div className="mt-3"><Button disabled={busy} onClick={() => act(async () => {
              await api.paperCreate(form.name, form.preset, { market: { symbol: form.symbol, timeframe: form.timeframe } });
              setAccount(form.name);
              accounts.reload();
            })}>Create account</Button></div>
          </Panel>
        </div>

        {s && (
          <>
            <Panel title={`${s.name} · ${s.symbol} ${s.timeframe} · ${s.strategy}`} right={
              <span className="flex flex-wrap gap-2">
                <Button disabled={busy} onClick={() => act(async () => { await api.paperStep(s.name); await load(s.name); accounts.reload(); })}>{busy ? "Working…" : "Process new candles"}</Button>
                {s.halted && <Button kind="ghost" disabled={busy} onClick={() => act(async () => { await api.paperResume(s.name); await load(s.name); })}>Resume trading</Button>}
              </span>}>
              <StatGrid cols={6}>
                <StatCard label="Virtual balance" value={money(s.balance)} hint={`started ${money(s.starting_balance)}`} />
                <StatCard label="Virtual equity" value={money(s.equity)} />
                <StatCard label="P&L" value={signedMoney(s.pnl)} tone={tone(s.pnl)} />
                <StatCard label="Drawdown" value={pct(s.drawdown_pct)} tone={s.drawdown_pct > 0 ? "down" : "neutral"} hint={`limit ${s.limits.max_account_drawdown_percent}%`} />
                <StatCard label="Exposure" value={`${num(s.exposure_leverage, 2)}×`} hint={`margin ${pct(s.margin_usage_pct)}`} />
                <StatCard label="Risk status" value={s.halted ? "HALTED" : "OK"} tone={s.halted ? "down" : "up"} hint={`last bar ${when(s.last_bar)}`} />
              </StatGrid>
              {s.halted && <p className="mt-3 text-sm text-accent-down">{s.halted}. Nothing opens until you resume.</p>}
            </Panel>
            <Panel title="Open basket">{s.open_basket ? <BasketStatus b={s.open_basket} /> : <p className="text-sm text-base-muted">No open basket: waiting for the next entry signal.</p>}</Panel>
            {s.equity_curve.length > 1 && <Panel title="Virtual equity"><EquityChart curve={s.equity_curve} /></Panel>}
            <Panel title="Basket history">
              <Table head={["Basket", "Dir", "Opened", "Closed", "Positions", "P&L", "MAE", "Closed by"]} align={["l", "l", "l", "l", "r", "r", "r", "l"]}
                rows={s.baskets.map((b) => [b.uid, <Direction d={b.direction} />, when(b.opened_at), when(b.closed_at), b.positions,
                  <span className={b.pnl < 0 ? "text-accent-down" : "text-accent-up"}>{signedMoney(b.pnl)}</span>, money(b.mae), label(b.close_reason)])}
                empty="No completed baskets yet." />
            </Panel>
            <Panel title="Risk events">
              <Table head={["Time", "Event", "Detail"]} rows={s.risk_events.map((e) => [when(e.ts), label(e.kind), <span className="whitespace-normal">{e.detail}</span>])} empty="None." />
            </Panel>
          </>
        )}
      </Page>
    </>
  );
}
