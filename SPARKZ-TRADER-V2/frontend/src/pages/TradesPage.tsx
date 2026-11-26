import { useCallback, useEffect, useState } from "react";
import { EquityChart } from "../charts/Charts";
import { money, num, pct, signedMoney, tone, when } from "../components/format";
import { Empty, ErrorBox, Page, PageHeader, Panel, StatCard, StatGrid, Table } from "../components/ui";
import { Dict, get } from "../services/api";

const MODES: [string, string][] = [["MT5_DEMO", "MT5 demo"], ["MT5_LIVE", "MT5 real"], ["SIMULATION", "Paper simulation"]];
const REASONS: Record<string, string> = { TARGET: "take profit", LOSS_LIMIT: "loss limit", BROKER_CLOSED: "closed in MT5",
  ACCOUNT_DRAWDOWN: "drawdown stop", MANUAL: "closed by you", END_OF_DATA: "end", TIME_STOP: "time stop" };

function duration(m: unknown): string {
  if (typeof m !== "number") return "—";
  return m >= 1440 ? `${(m / 1440).toFixed(1)} d` : m >= 60 ? `${(m / 60).toFixed(1)} h` : `${Math.round(m)} min`;
}

export default function TradesPage() {
  const [mode, setMode] = useState<string | null>(null);
  const [d, setD] = useState<Dict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { const r = await get(`/api/trades${mode ? `?mode=${mode}` : ""}`); setD(r); setError(null); if (!mode) setMode(r.mode); }
    catch (e) { setError((e as Error).message); }
  }, [mode]);
  useEffect(() => { load(); const t = setInterval(load, 30_000); return () => clearInterval(t); }, [load]);

  const s: Dict = d?.summary ?? {};
  const cur = d?.currency ? ` ${d.currency}` : "";
  const curve = d ? [{ time: d.since ?? d.trades?.at(-1)?.opened_at, equity: d.starting_balance },
    ...[...(d.trades ?? [])].reverse().map((t: Dict) => ({ time: t.closed_at, equity: t.balance_after }))].filter((p) => p.time && p.equity != null) : [];
  const now: Dict | null = d?.now ?? null;
  return (
    <>
      <PageHeader title="Trade history" subtitle="Every SPARKZ trade on the robot's market, kept across restarts, with your running total against the balance the robot started from." />
      <Page>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex border border-base-border" role="group" aria-label="Account">
            {MODES.map(([k, n]) => (
              <button key={k} onClick={() => setMode(k)}
                className={`px-3 py-2 text-sm ${mode === k ? "bg-accent-brand text-white font-semibold" : "text-base-muted hover:text-base-text"}`}>
                {n}{d?.current_mode === k ? " (running)" : ""}
              </button>
            ))}
          </div>
          <span className="text-xs text-base-muted ml-auto">{d ? `${d.symbol} · ${d.runs} session${d.runs === 1 ? "" : "s"}${d.since ? ` since ${when(d.since)}` : ""}` : ""}</span>
        </div>
        {error && <ErrorBox error={error} />}
        {!d ? null : (
          <>
            <StatGrid cols={4}>
              <StatCard label="Started with" value={d.starting_balance != null ? money(d.starting_balance) + cur : "—"}
                hint={d.mode === "SIMULATION" ? "paper account" : "MT5 balance when the robot first synced"} />
              <StatCard label="Net result" value={signedMoney(s.net) + cur} tone={tone(s.net)} hint={s.net_pct != null ? pct(s.net_pct, 2, true) + " of the start" : undefined} />
              <StatCard label="Won / lost" value={`${money(s.total_won)} / ${money(s.total_lost)}`} hint={`${s.wins ?? 0} winning · ${s.losses ?? 0} losing trades`} />
              <StatCard label="Win rate" value={s.win_rate_pct != null ? `${num(s.win_rate_pct, 1)}%` : "—"}
                hint={`${s.trades ?? 0} trades${s.profit_factor != null ? ` · profit factor ${num(s.profit_factor, 2)}` : ""}`} />
            </StatGrid>
            <StatGrid cols={4}>
              <StatCard label="Average trade" value={s.average != null ? signedMoney(s.average) : "—"} tone={tone(s.average)} hint={`1 R = ${money(d.r_unit_usd, 0)} (the loss limit)`} />
              <StatCard label="Best / worst" value={s.best != null ? `${signedMoney(s.best)} / ${signedMoney(s.worst)}` : "—"} />
              <StatCard label="Balance by these trades" value={d.starting_balance != null ? money(d.starting_balance + (s.net ?? 0)) + cur : "—"} hint="start + SPARKZ results" />
              <StatCard label="MT5 balance now" value={now?.mt5_balance != null ? money(now.mt5_balance) + cur : "—"}
                hint={now?.mt5_balance != null ? `equity ${money(now.mt5_equity)}${now.mt5_change_since_start != null ? ` · ${signedMoney(now.mt5_change_since_start)} since start (all trades, deposits included)` : ""}` : now ? "MT5 not connected" : "not the running account"} />
            </StatGrid>
            {d.open_trade && (
              <Panel title="Open trade now">
                <p className="text-sm">
                  <b className={d.open_trade.direction === "BUY" ? "text-accent-up" : "text-accent-down"}>{d.open_trade.direction}</b> {num(d.open_trade.total_lots, 2)} lot at {num(d.open_trade.average_entry, 2)}
                  {" · "}floating <b className={tone(d.open_trade.floating_pnl) === "up" ? "text-accent-up" : "text-accent-down"}>{signedMoney(d.open_trade.floating_pnl)}</b>
                  {d.open_trade.target_price != null && ` · target ${num(d.open_trade.target_price, 2)}`}{d.open_trade.stop_price != null && ` · loss limit ${num(d.open_trade.stop_price, 2)}`}
                </p>
              </Panel>
            )}
            <Panel title="Balance after each trade">
              {curve.length < 2 ? <Empty>No closed trades yet on this account.</Empty> : <EquityChart points={curve} initial={d.starting_balance} height={240} />}
            </Panel>
            <Panel title={`Trades (${s.trades ?? 0}, newest first)`}>
              <Table head={["Closed", "Side", "Lots", "Entry", "Exit", "Result", "R", "Why it closed", "Held", "Running total"]}
                align={["l", "l", "r", "r", "r", "r", "r", "l", "r", "r"]}
                rows={(d.trades ?? []).map((t: Dict) => [when(t.closed_at),
                  <span className={t.direction === "BUY" ? "text-accent-up" : "text-accent-down"}>{t.direction}</span>,
                  num(t.lots, 2), num(t.entry, 2), num(t.exit, 2),
                  <span className={t.pnl > 0 ? "text-accent-up" : "text-accent-down"}>{signedMoney(t.pnl)}</span>,
                  num(t.r, 2), REASONS[t.close_reason] ?? t.close_reason, duration(t.duration_minutes),
                  <span className={t.running_total >= 0 ? "text-accent-up" : "text-accent-down"}>{signedMoney(t.running_total)}</span>])}
                empty="No closed SPARKZ trades on this account yet. The daily robot trades about twice a month." />
            </Panel>
            <p className="text-xs text-base-muted">Results are the robot's own record of each SPARKZ trade. The MT5 balance also moves with anything else on the account, such as manual trades, deposits or swap.</p>
          </>
        )}
      </Page>
    </>
  );
}
