import { EquityChart, PriceChart } from "../charts/Charts";
import { label, money, num, pct, price, signedMoney, tone, when } from "../components/format";
import { Direction, Empty, ErrorBox, KeyValues, Page, PageHeader, Panel, Regime, StatCard, StatGrid, Table } from "../components/ui";
import { useEffect, useState } from "react";
import { Dict, get } from "../services/api";
import { Decision, useAssistant } from "./AssistantPage";

const STATUS_TONE: Record<string, string> = {
  ANALYZING: "text-base-text", WAITING: "text-base-muted", ENTRY: "text-accent-warn", ADDING_POSITION: "text-accent-warn",
  MANAGING_BASKET: "text-accent-oxford", TARGET_REACHED: "text-accent-up", CLOSING: "text-accent-warn",
  COOLDOWN: "text-base-muted", STOPPED: "text-accent-down",
};

export function StatusWord({ s }: { s: string }) {
  return <span className={`font-display text-3xl ${STATUS_TONE[s] ?? ""}`}>{s.replace(/_/g, " ")}</span>;
}

function Market({ d }: { d: Dict }) {
  const m = d.market;
  if (!m) return <Panel title="Live market"><Empty>Waiting for the first price…</Empty></Panel>;
  const prov = d.provider ?? {};
  return (
    <Panel title="Live market" right={<span className="text-xs text-base-muted">{prov.name}{prov.delayed ? " · delayed" : ""}{!prov.live ? " · not live market data" : ""}</span>}>
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 mb-3">
        <span className="font-display text-2xl">{m.symbol}</span>
        <span className="font-mono-nums text-3xl">{price(m.mid)}</span>
        <span className="text-sm text-base-muted font-mono-nums">bid {price(m.bid)} · ask {price(m.ask)} · spread {num(m.spread, 2)}</span>
      </div>
      <div className="grid md:grid-cols-2 gap-x-8">
        <KeyValues items={[
          ["Last update", when(m.timestamp)],
          ["Regime", <Regime r={m.regime} />],
          ["Trend / volatility", <span><Regime r={m.trend_regime} /> · <Regime r={m.vol_regime} /></span>],
          ["ATR", num(m.atr, 2)],
          ["RSI", num(m.rsi, 1)],
        ]} />
        <KeyValues items={[
          ["EMA 20 / 50", `${price(m.ema20)} / ${price(m.ema50)}`],
          ["EMA 200", price(m.ema200)],
          ["MACD / signal", `${num(m.macd, 2)} / ${num(m.macd_signal, 2)}`],
          ["ADX", num(m.adx, 1)],
          ["Volatility (pctile)", `${num((m.volatility ?? 0) * 100, 3)}% (${num((m.vol_percentile ?? 0) * 100, 0)})`],
        ]} />
      </div>
      {prov.notes?.length > 0 && <p className="text-[11px] text-base-muted mt-3">{prov.notes.join(" · ")}</p>}
    </Panel>
  );
}

function Robot({ d }: { d: Dict }) {
  const a = d.analysis;
  const sig = d.signal;
  return (
    <Panel title="Robot status">
      <StatusWord s={d.status} />
      {d.emergency_stop && <p className="text-sm text-accent-down mt-2">Emergency stop: {d.emergency_stop}</p>}
      {d.halted && <p className="text-sm text-accent-down mt-2">Halted: {d.halted}</p>}
      {!d.data_ok && <p className="text-sm text-accent-warn mt-2">Market data stale, new entries blocked: {d.data_reason}</p>}
      <div className="mt-4">
        <KeyValues items={[
          ["Strategy", `${d.strategy}${d.video_style_mode ? " · VIDEO_STYLE_MODE" : ""}`],
          ["Preset", d.preset],
          ["Last decision", sig ? <Direction d={sig.action} /> : "—"],
          ["Analysis direction", a ? <Direction d={a.direction} /> : "—"],
          ["Rule agreement", a ? `${num(a.confidence * 100, 0)}%` : "—"],
          ["Bars processed", d.bar],
        ]} />
        <p className="text-[11px] text-base-muted mt-2">Rule agreement = share of 7 checks that agree. It is not a probability of profit.</p>
        {sig?.reasons?.length > 0 && (
          <ul className="text-xs text-base-muted mt-3 list-disc pl-4 space-y-0.5">{sig.reasons.slice(0, 6).map((r: string) => <li key={r}>{r}</li>)}</ul>
        )}
      </div>
    </Panel>
  );
}

function Basket({ d }: { d: Dict }) {
  const b = d.basket;
  if (!b) return <Panel title="Current basket"><Empty>No open basket. {d.pending_orders?.length ? "An order is pending." : ""}</Empty></Panel>;
  const r = b.recovery ?? {};
  return (
    <Panel title="Current basket" right={<span className="font-mono-nums text-xs">{b.basket_id}</span>}>
      <StatGrid cols={4}>
        <StatCard label="Direction" value={b.direction} tone={b.direction === "BUY" ? "up" : "down"} />
        <StatCard label="Positions" value={`${b.positions} · ${num(b.total_lots, 2)} lots`} />
        <StatCard label="Average entry" value={price(b.average_entry)} />
        <StatCard label="Floating P&L" value={signedMoney(b.floating_pnl)} tone={tone(b.floating_pnl)} />
        <StatCard label="Target" value={money(b.target_usd)} hint={`at ${price(b.target_price)}`} />
        <StatCard label="Loss limit" value={money(-b.loss_limit_usd)} tone="down" hint={`at ${price(b.stop_price)}`} />
        <StatCard label="MAE / MFE" value={`${signedMoney(b.mae)} / ${signedMoney(b.mfe)}`} />
        <StatCard label="Exposure · margin" value={money(b.exposure_usd, 0)} hint={`margin ${money(b.margin_usd)}`} />
      </StatGrid>
      {b.adds_blocked && <p className="text-sm text-accent-warn mt-3">Grid stopped: {b.adds_blocked}</p>}
      <div className="mt-4">
        <Table head={["#", "Lots", "Entry", "Time", "P&L", "Reason"]} align={["l", "r", "r", "l", "r", "l"]}
          rows={(d.positions ?? []).map((p: Dict) => [p.seq, num(p.lots, 2), price(p.entry_price), when(p.entry_time),
            <span className={tone(p.pnl) === "down" ? "text-accent-down" : "text-accent-up"}>{signedMoney(p.pnl)}</span>, <span className="text-xs text-base-muted">{p.reason}</span>])} />
      </div>
      <div className="mt-4 text-sm">
        <div className="stat-label mb-1">Recovery analysis (information only, nothing is added automatically)</div>
        <KeyValues items={[
          ["Move to break-even", r.break_even ? `${num(r.break_even.move, 2)} (${num(r.break_even.move_atr, 1)} ATR)` : "—"],
          ["Move to target", r.target ? `${num(r.target.move, 2)} (${num(r.target.move_atr, 1)} ATR)` : "—"],
          ["Move to loss limit", r.loss_limit ? `${num(r.loss_limit.move, 2)} (${num(r.loss_limit.move_atr, 1)} ATR)` : "—"],
          ["Extra lots to pull break-even within 1 ATR", r.extra_lots_for_be_within_1atr ? `${r.extra_lots_for_be_within_1atr.lots} lots (margin after ${money(r.extra_lots_for_be_within_1atr.margin_after_usd)})` : "not needed / not possible"],
        ]} />
      </div>
    </Panel>
  );
}

function Account({ d }: { d: Dict }) {
  const a = d.account;
  return (
    <Panel title={a.label} right={<span className="text-xs text-accent-warn">simulated · no real money</span>}>
      <StatGrid cols={4}>
        <StatCard label="Balance" value={money(a.balance)} />
        <StatCard label="Equity" value={money(a.equity)} tone={tone(a.equity - a.initial_balance)} />
        <StatCard label="Floating P&L" value={signedMoney(a.floating_pnl)} tone={tone(a.floating_pnl)} />
        <StatCard label="Realized P&L" value={signedMoney(a.realized_pnl)} tone={tone(a.realized_pnl)} />
        <StatCard label="Used / free margin" value={money(a.used_margin)} hint={`free ${money(a.free_margin)}`} />
        <StatCard label="Margin level" value={a.margin_level_pct ? pct(a.margin_level_pct, 0) : "—"} />
        <StatCard label="Daily P&L" value={signedMoney(a.daily_pnl)} tone={tone(a.daily_pnl)} />
        <StatCard label="Drawdown (max)" value={pct(-a.drawdown_pct)} tone="down" hint={`max ${pct(-a.max_drawdown_pct)}`} />
      </StatGrid>
      <div className="mt-4"><EquityChart points={d.equity_curve ?? []} initial={a.initial_balance} /></div>
    </Panel>
  );
}

export function EventList({ events, max = 40 }: { events: Dict[]; max?: number }) {
  if (!events?.length) return <Empty>No events yet.</Empty>;
  return (
    <ol className="text-sm divide-y divide-base-border/50">
      {events.slice(-max).reverse().map((e) => (
        <li key={e.seq} className="py-1.5 grid grid-cols-[7.5rem_9rem_minmax(0,1fr)] gap-2">
          <span className="text-xs text-base-muted font-mono-nums">{when(e.time)}</span>
          <span className={`text-[11px] font-semibold tracking-wide ${/STOP|HALT|LOSS|ERROR|REJECT|STALE/.test(e.type) ? "text-accent-down" : /TARGET|OPENED|ADDED/.test(e.type) ? "text-accent-up" : "text-base-muted"}`}>{e.type}</span>
          <span className="min-w-0 break-words">{e.message}</span>
        </li>
      ))}
    </ol>
  );
}

function AssistantSummary() {
  const { data } = useAssistant();
  const r = data?.report;
  return (
    <Panel title="Market assistant" right={<a href="#assistant" className="text-xs underline">full analysis</a>}>
      {r ? <Decision r={r} compact /> : <Empty>{data?.error ?? "Analysing the market…"}</Empty>}
    </Panel>
  );
}

/** Demo/live results against the history study (GET /api/execution/report), refreshed every minute. */
function ExecutionReport() {
  const [r, setR] = useState<Dict | null>(null);
  useEffect(() => {
    const load = () => get("/api/execution/report").then(setR).catch(() => {});
    load(); const t = setInterval(load, 60_000); return () => clearInterval(t);
  }, []);
  if (!r) return null;
  const res = r.results ?? {}, ex = r.execution, ref = r.history_reference;
  return (
    <Panel title="Results vs. the backtests">
      <p className="text-sm mb-3">{r.verdict}</p>
      <div className="grid md:grid-cols-2 gap-x-8 gap-y-4">
        <KeyValues items={[
          ["Closed baskets", res.baskets],
          ["Win rate", res.win_rate_pct == null ? "—" : `${num(res.win_rate_pct, 1)}%`],
          ["Average result", res.avg_r == null ? "—" : `${num(res.avg_r, 3)} R (1 R = ${money(res.r_unit_usd)})`],
          ["Net", signedMoney(res.net_usd)],
          ["History study", ref ? `${num(ref.win_rate_pct, 1)}% wins, ${num(ref.expectancy_r, 3)} R per trade (${ref.trades} trades, spread ${ref.spread_assumed})` : "—"],
        ]} />
        {ex && <KeyValues items={[
          ["MT5 fills", ex.fills],
          ["Slippage vs. quote (median / worst)", ex.slippage?.count ? `${num(ex.slippage.median, 3)} / ${num(ex.slippage.max, 3)}` : "—"],
          ["Spread when ordering (median)", ex.spread_at_order?.count ? num(ex.spread_at_order.median, 3) : "—"],
          ["Decision to fill (median)", ex.delay_seconds?.count ? `${num(ex.delay_seconds.median, 1)} s` : "—"],
        ]} />}
      </div>
    </Panel>
  );
}

/** Shown only when orders go to MT5 (EXECUTION_MODE=MT5_DEMO / MT5_LIVE): the broker account as the bridge reports it. */
function Mt5Execution({ x }: { x: Dict }) {
  const a = x.account;
  const real = x.account_type === "REAL";
  const dl = x.daily;
  return (
    <Panel title={`MT5 execution · ${x.account_type === "?" ? "waiting for the account" : `${x.account_type} account`}`}
      right={<span className="text-xs text-accent-down font-semibold">{real ? "REAL MONEY · orders are sent to MT5" : "orders are sent to MT5"}</span>}>
      <ErrorBox error={x.problem ? `Orders are refused right now: ${x.problem}` : null} />
      {a ? (
        <div className="grid gap-3">
          <StatGrid cols={4}>
            <StatCard label="MT5 account" value={a.demo ? "DEMO" : "REAL"} tone={a.demo ? "neutral" : "down"} hint={a.currency} />
            <StatCard label="MT5 balance" value={money(a.balance)} />
            <StatCard label="MT5 equity" value={money(a.equity)} hint={`free margin ${money(a.margin_free)}`} />
            <StatCard label="Today (UTC)" value={dl ? `${num(-dl.loss_pct, 2)}%` : "—"} tone={dl?.stopped ? "down" : "neutral"}
              hint={dl ? `daily loss stop at -${dl.limit_pct}% · ${(a.positions ?? []).length} SPARKZ position(s), max ${x.max_live_lots} lots` : ""} />
          </StatGrid>
          <Table head={["Ticket", "Side", "Lots", "Open price", "Protective stop", "Profit"]} align={["l", "l", "r", "r", "r", "r"]}
            rows={(a.positions ?? []).map((p: Dict) => [p.ticket, <Direction d={p.side} />, p.volume, price(p.price_open), price(p.sl), signedMoney(p.profit)])}
            empty="No SPARKZ position open in MT5." />
        </div>
      ) : <Empty>Waiting for the bridge's account report (run the bridge with --trade).</Empty>}
      <p className="text-[11px] text-base-muted mt-3">Every study of this robot lost money after costs. STOP ROBOT blocks new orders; positions stay open in MT5 with their protective stop, and can be closed from the dashboard or in MT5.</p>
    </Panel>
  );
}

export default function LivePage({ d }: { d: Dict | null }) {
  if (!d) return <Page><PageHeader title="Live" /><Empty>Connecting to the robot…</Empty></Page>;
  return (
    <>
      <PageHeader title="Live" subtitle={`${d.market?.symbol ?? ""} ${d.preset} · ${d.execution?.kind?.startsWith("MT5") ? `orders go to your MT5 ${d.execution.account_type === "REAL" ? "REAL" : "DEMO"} account via ${d.provider?.name ?? "the bridge"}.` : `paper trading on ${d.provider?.name ?? "?"}. Every order is simulated.`}`} />
      <Page>
        <div className="grid xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-5">
          <Market d={d} />
          <Robot d={d} />
        </div>
        {d.execution?.kind?.startsWith("MT5") && <Mt5Execution x={d.execution} />}
        {d.execution?.kind?.startsWith("MT5") && <ExecutionReport />}
        <AssistantSummary />
        <Panel title="Price"><PriceChart candles={d.candles ?? []} basket={d.basket} live={d.market?.mid} /></Panel>
        <Basket d={d} />
        <Account d={d} />
        <div className="grid xl:grid-cols-2 gap-5">
          <Panel title="Recent baskets">
            <Table head={["Basket", "Dir", "Pos", "P&L", "MAE", "Reason", "Closed"]} align={["l", "l", "r", "r", "r", "l", "l"]}
              rows={(d.recent_baskets ?? []).slice().reverse().map((b: Dict) => [b.uid, <Direction d={b.direction} />, b.positions,
                <span className={b.pnl < 0 ? "text-accent-down" : "text-accent-up"}>{signedMoney(b.pnl)}</span>, signedMoney(b.mae), label(b.close_reason), when(b.closed_at)])}
              empty="No completed baskets yet." />
          </Panel>
          <Panel title="Event log (latest)"><EventList events={d.events ?? []} /></Panel>
        </div>
      </Page>
    </>
  );
}
