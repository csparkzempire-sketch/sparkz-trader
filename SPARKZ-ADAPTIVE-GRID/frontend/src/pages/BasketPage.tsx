import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import type { PaperBasket, PaperStatus } from "../types/api";
import { Direction, Empty, ErrorBox, KeyValues, Page, PageHeader, Panel, Table } from "../components/ui";
import { useAppState, useLoad } from "../components/state";
import { money, num, price, when } from "../components/format";

export function BasketStatus({ b }: { b: PaperBasket }) {
  return (
    <div className="grid lg:grid-cols-[320px_minmax(0,1fr)] gap-5">
      <div className="panel p-4 bg-base-tint/60">
        <div className="stat-label mb-3">Basket status · {b.basket_id}</div>
        <KeyValues items={[
          ["Direction", <Direction d={b.direction} />],
          ["Positions", String(b.positions)],
          ["Total size", `${num(b.total_lots, 2)} lots`],
          ["Average entry", price(b.average_entry)],
          ["Basket target", b.target_usd !== null ? money(b.target_usd) : `${price(b.target_distance)} beyond avg entry`],
          ["Loss limit", money(b.loss_limit)],
          ["Worst open P&L so far (MAE)", money(b.mae)],
          ["Best open P&L so far (MFE)", money(b.mfe)],
          ["Opened", when(b.opened_at)],
        ]} />
      </div>
      <Table head={["#", "Lots", "Fill price", "Trigger (mid)", "Time"]} align={["l", "r", "r", "r", "l"]}
        rows={b.entries.map((e) => [e.seq, num(e.lots, 2), price(e.entry_price), price(e.ref_price), when(e.entry_time)])} />
    </div>
  );
}

export default function BasketPage() {
  const { account, setAccount } = useAppState();
  const accounts = useLoad(() => api.paperAccounts(), []);
  const [s, setS] = useState<PaperStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const name = account ?? accounts.data?.[0]?.name ?? null;
  useEffect(() => {
    if (name) api.paperStatus(name).then(setS).catch((e) => setError(e.message));
  }, [name]);

  return (
    <>
      <PageHeader title="Basket" subtitle="The paper account's current basket: every position in it is managed and closed together." />
      <Page>
        <ErrorBox error={error} />
        {!name && <Empty>No paper account yet. <Link className="text-accent-brass font-semibold" to="/paper">Create one</Link> to follow a live basket; backtest baskets are on the Performance page.</Empty>}
        {accounts.data && accounts.data.length > 1 && (
          <div className="flex flex-wrap gap-2">{accounts.data.map((a) => (
            <button key={a.name} onClick={() => setAccount(a.name)} className={`text-sm px-3 py-1.5 border ${a.name === name ? "border-accent-brass text-base-text" : "border-base-border text-base-muted"}`}>{a.name}</button>
          ))}</div>
        )}
        {s && (
          <Panel title={`${s.name} · ${s.symbol} ${s.timeframe}`}>
            {s.open_basket ? <BasketStatus b={s.open_basket} /> : <p className="text-sm text-base-muted">No basket open. The strategy is waiting for a new market-analysis signal{s.halted ? " (the account is halted)" : ""}.</p>}
          </Panel>
        )}
        {s && s.baskets.length > 0 && (
          <Panel title="Most recent completed basket">
            <Table head={["Basket", "Dir", "Opened", "Closed", "Positions", "P&L", "MAE", "Closed by"]} align={["l", "l", "l", "l", "r", "r", "r", "l"]}
              rows={s.baskets.slice(0, 5).map((b) => [b.uid, <Direction d={b.direction} />, when(b.opened_at), when(b.closed_at), b.positions, money(b.pnl), money(b.mae), b.close_reason])} />
          </Panel>
        )}
      </Page>
    </>
  );
}
