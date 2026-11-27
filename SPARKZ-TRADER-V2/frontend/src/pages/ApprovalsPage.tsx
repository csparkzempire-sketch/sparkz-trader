import { useCallback, useEffect, useState } from "react";
import { num, signedMoney, when } from "../components/format";
import { Button, Empty, ErrorBox, Page, PageHeader, Panel, StatCard, StatGrid, Table } from "../components/ui";
import { Dict, get, post } from "../services/api";

const STATUS: Record<string, string> = { PENDING: "waiting for you", SENT: "sent to MT5", OPEN: "open in MT5", CLOSING: "closing",
  CLOSED: "closed", REJECTED: "rejected", EXPIRED: "expired", FAILED: "not placed" };

function left(iso: string): string {
  const s = (Date.parse(iso) - Date.now()) / 1000;
  if (s <= 0) return "expired";
  return s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min left` : `${Math.round(s / 60)} min left`;
}

function Proposal({ p, onAct, busy }: { p: Dict; onAct: (id: number, what: string) => void; busy: boolean }) {
  const d = p.digits ?? 5;
  const buy = p.side === "BUY";
  return (
    <div className="panel p-4 grid gap-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className={`text-2xl font-semibold ${buy ? "text-accent-up" : "text-accent-down"}`}>{p.side}</span>
        <span className="text-2xl">{p.symbol}</span>
        <span className="text-sm text-base-muted">#{p.id} · {p.timeframe} · {String(p.strategy ?? "").replace(/_/g, " ")} · confidence {num(p.confidence_pct, 0)}% · bias {p.bias}</span>
        <span className="ml-auto text-xs text-accent-warn">{left(p.expires_at)}</span>
      </div>
      <StatGrid cols={4}>
        <StatCard label="Entry (planned)" value={num(p.entry, d)} hint="at market when you approve" />
        <StatCard label="Stop loss" value={num(p.stop, d)} tone="down" />
        <StatCard label="Take profit" value={num(p.target, d)} tone="up" hint={`reward 1:${num(p.risk_reward, 2)}`} />
        <StatCard label="Risk if approved" value={`≈ ${num(p.risk_per_trade, 0)}`} hint="account currency, at the stop loss" />
      </StatGrid>
      <ul className="text-sm text-base-muted list-disc pl-5">
        {(p.reasons ?? []).map((r: string) => <li key={r}>{r}</li>)}
        {p.past_samples ? <li>Past record of this setup on this market: target first in {num(p.past_hit_rate_pct, 0)}% of {p.past_samples}</li> : null}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => onAct(p.id, "approve")} disabled={busy}>Approve: place this trade</Button>
        <Button kind="ghost" onClick={() => onAct(p.id, "reject")} disabled={busy}>Reject</Button>
        <a className="text-xs underline text-base-muted self-center" href="#chart" onClick={() => { try { localStorage.setItem("sparkz-chart-symbol", p.symbol); localStorage.setItem("sparkz-chart-tf", p.timeframe); } catch { /* */ } }}>see it on the chart</a>
      </div>
    </div>
  );
}

export default function ApprovalsPage() {
  const [d, setD] = useState<Dict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setD(await get("/api/approvals")); setError(null); } catch (e) { setError((e as Error).message); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 5_000); return () => clearInterval(t); }, [load]);

  const act = async (id: number, what: string) => {
    if (what === "approve" && !confirm(`Place proposal #${id} in your MT5 account now?`)) return;
    if (what === "close" && !confirm(`Close trade #${id} in MT5 now, at the market price?`)) return;
    setBusy(true); setNote(null);
    try { await post(`/api/approvals/${id}/${what}`); setNote(what === "approve" ? `#${id} sent to MT5.` : what === "close" ? `Closing #${id}.` : `#${id} rejected.`); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); load(); }
  };
  const scan = async () => { setBusy(true); try { const r = await post("/api/approvals/scan"); setNote(r.new?.length ? `${r.new.length} new proposal(s).` : "No new setup: the assistant waits for each candle close."); } catch (e) { setError((e as Error).message); } finally { setBusy(false); load(); } };

  const c: Dict = d?.config ?? {};
  return (
    <>
      <PageHeader title="Trade approvals" subtitle="The assistant scans every market your MT5 bridge watches (forex, crypto, indices, stocks, commodities) and proposes trades. Nothing is placed until you approve, here or with the Approve button on Telegram." />
      <Page>
        {error && <ErrorBox error={error} />}
        {note && <div className="text-sm text-accent-up">{note}</div>}
        {d && (
          <>
            {!d.trading && <ErrorBox error="Approved trades can't be placed: the server runs in paper simulation. Run SETUP_SPARKZ.bat and choose the MT5 demo account." />}
            {d.trading && d.problem && <ErrorBox error={`MT5 is not ready to trade: ${d.problem}`} />}
            <StatGrid cols={4}>
              <StatCard label="Markets scanned" value={String(d.markets?.length ?? 0)} hint={(d.markets ?? []).join(" · ") || "none: add markets to the bridge's watch list"} />
              <StatCard label="Timeframe" value={c.timeframe ?? "—"} hint={`a proposal after each ${c.timeframe} close · expires in ${num(c.expiry_minutes, 0)} min`} />
              <StatCard label="Risk per approved trade" value={`≈ ${num(c.risk, 0)}`} hint={`at most ${c.max_open} open at once`} />
              <StatCard label="Approved trades closed" value={String(d.summary?.closed ?? 0)}
                hint={d.summary?.closed ? `${d.summary.won} won · net ${signedMoney(d.summary.net_profit)} ${d.summary.currency}` : "none yet"} />
            </StatGrid>
            <div className="flex flex-wrap items-center gap-3 text-xs text-base-muted">
              <Button kind="ghost" onClick={scan} disabled={busy}>Scan now</Button>
              <span>Last scan {d.last_scan ? when(d.last_scan) : "—"} · Telegram buttons {d.telegram ? (d.telegram.buttons ? "on" : "off") : "not set up"}
                {d.telegram?.last_error ? ` (last error: ${d.telegram.last_error})` : ""}</span>
              {Object.keys(d.scan_errors ?? {}).length > 0 && <span className="text-accent-warn">scan errors: {Object.entries(d.scan_errors).map(([k, v]) => `${k}: ${v}`).join("; ")}</span>}
            </div>

            <Panel title={`Waiting for your approval (${d.pending?.length ?? 0})`}>
              {!(d.pending ?? []).length ? <Empty>No proposal now. The assistant only proposes when a {c.timeframe} candle closes with a clear setup; most closes give none.</Empty> : (
                <div className="grid gap-4">{d.pending.map((p: Dict) => <Proposal key={p.id} p={p} onAct={act} busy={busy} />)}</div>
              )}
            </Panel>

            <Panel title={`Approved trades now (${d.active?.length ?? 0})`}>
              <Table head={["#", "Market", "Side", "Lots", "Filled", "Stop loss", "Take profit", "Now", "Profit", "Status", ""]}
                align={["l", "l", "l", "r", "r", "r", "r", "r", "r", "l", "l"]}
                rows={(d.active ?? []).map((p: Dict) => [p.id, p.symbol, <span className={p.side === "BUY" ? "text-accent-up" : "text-accent-down"}>{p.side}</span>,
                  num(p.volume, 2), num(p.fill_price, p.digits ?? 5), num(p.stop, p.digits ?? 5), num(p.target, p.digits ?? 5), num(p.current, p.digits ?? 5),
                  <span className={(p.profit ?? 0) >= 0 ? "text-accent-up" : "text-accent-down"}>{p.profit != null ? signedMoney(p.profit) : "—"}</span>,
                  STATUS[p.status] ?? p.status,
                  p.status === "OPEN" ? <button className="text-xs underline" onClick={() => act(p.id, "close")} disabled={busy}>close now</button> : null])}
                empty="No approved trade is open." />
              {(d.untracked_positions ?? []).length > 0 && (
                <p className="text-xs text-accent-warn mt-2">MT5 also has {d.untracked_positions.length} approved-trade position(s) this server isn't following (e.g. after a data reset): {d.untracked_positions.map((x: Dict) => `${x.symbol} ${x.side} ${x.volume}`).join(", ")}. They keep their stop loss and take profit in MT5.</p>
              )}
            </Panel>

            <Panel title="History">
              <Table head={["#", "Time", "Market", "Side", "TF", "Confidence", "Result", "Note"]} align={["l", "l", "l", "l", "l", "r", "l", "l"]}
                rows={(d.history ?? []).map((p: Dict) => [p.id, when(p.created_at), p.symbol, p.side, p.timeframe, `${num(p.confidence_pct, 0)}%`,
                  p.status === "CLOSED" ? <span className={(p.profit ?? 0) >= 0 ? "text-accent-up" : "text-accent-down"}>{signedMoney(p.profit)} ({p.close_reason})</span> : STATUS[p.status] ?? p.status,
                  <span className="text-xs text-base-muted">{p.events?.at(-1)?.note}</span>])}
                empty="Nothing yet." />
            </Panel>
            <p className="text-xs text-base-muted">Tested so far: gold daily (positive, not yet proven) and EURUSD, GBPUSD, USDJPY daily (see the technical report). Other markets are untested. Each approved trade is sized by MT5 so its stop loss costs about the risk shown, never above the bridge's --max-approval-lots; stop loss and take profit are set inside MT5.</p>
          </>
        )}
      </Page>
    </>
  );
}
