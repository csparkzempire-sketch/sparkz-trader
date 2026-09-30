import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import type { BacktestSummary, GroupStats } from "../types/api";
import { Direction, Empty, ErrorBox, Loading, Page, PageHeader, Panel, Regime, StatCard, StatGrid, Table } from "../components/ui";
import { useAppState, useLoad } from "../components/state";
import { label, money, num, pct, price, signedMoney, tone, when } from "../components/format";
import { DrawdownChart, EquityChart, MaeScatter, PnlHistogram } from "../charts/Charts";

export function RegimeTable({ groups }: { groups: Record<string, GroupStats> }) {
  const rows = Object.entries(groups);
  return (
    <Table
      head={["Regime at entry", "Baskets", "Win rate", "Net P&L", "Avg P&L", "Profit factor", "Worst basket", "Avg MAE", "Avg positions"]}
      align={["l", "r", "r", "r", "r", "r", "r", "r", "r"]}
      rows={rows.map(([k, g]) => [
        <Regime r={k} />, g.baskets, pct(g.win_rate_pct, 1),
        <span className={tone(g.net_pnl) === "down" ? "text-accent-down" : "text-accent-up"}>{money(g.net_pnl)}</span>,
        money(g.avg_pnl), num(g.profit_factor), money(g.worst_basket), money(g.avg_mae), num(g.avg_positions, 1),
      ])}
      empty="No baskets."
    />
  );
}

export default function PerformancePage() {
  const { backtestId } = useAppState();
  const run = useLoad<BacktestSummary | null>(() => (backtestId ? api.getBacktest(backtestId) : Promise.resolve(null)), [backtestId]);
  const [page, setPage] = useState(0);

  if (!backtestId) {
    return (
      <>
        <PageHeader title="Performance" />
        <Page><Empty>No run selected. <Link className="text-accent-brass font-semibold" to="/backtest">Run or open a backtest</Link> first.</Empty></Page>
      </>
    );
  }
  const r = run.data;
  const m = r?.metrics;
  const mm = m?.mae_mfe;
  const cfg = r?.config;
  const baskets = r?.baskets ?? [];
  const PAGE = 25;

  return (
    <>
      <PageHeader
        title="Performance"
        subtitle={m ? `Run #${backtestId} · ${cfg?.name} · ${m.symbol} ${m.timeframe} · ${when(m.start)} to ${when(m.end)} · ${m.bars.toLocaleString()} bars` : undefined}
      />
      <Page>
        <ErrorBox error={run.error} />
        {run.loading && <Loading />}
        {m && mm && (
          <>
            <StatGrid cols={6}>
              <StatCard label="Ending balance" value={money(m.ending_balance)} hint={`started ${money(m.starting_balance)}`} />
              <StatCard label="Net P&L" value={money(m.net_pnl)} tone={tone(m.net_pnl)} hint={pct(m.return_pct, 2, true)} />
              <StatCard label="Baskets" value={String(m.baskets)} hint={`${m.winning_baskets} won · ${m.losing_baskets} lost`} />
              <StatCard label="Win rate" value={pct(m.win_rate_pct, 1)} />
              <StatCard label="Profit factor" value={num(m.profit_factor)} hint={`expectancy ${money(m.expectancy)} / basket`} />
              <StatCard label="Max drawdown" value={pct(m.max_drawdown_pct)} tone="down" hint={money(m.max_drawdown_usd)} />
              <StatCard label="Avg win / avg loss" value={`${money(m.avg_basket_profit)} / ${money(m.avg_basket_loss)}`} />
              <StatCard label="Largest win / loss" value={`${money(m.largest_basket_profit)} / ${money(m.largest_basket_loss)}`} />
              <StatCard label="Max consecutive losses" value={String(m.max_consecutive_losses)} />
              <StatCard label="Avg basket duration" value={`${num(m.avg_basket_bars, 1)} bars`} hint={`longest ${m.max_basket_bars ?? "—"}`} />
              <StatCard label="Positions per basket" value={num(m.avg_positions_per_basket, 2)} hint={`max ${m.max_positions_in_basket} · ${m.baskets_at_max_positions} baskets at the cap`} />
              <StatCard label="Sharpe / Sortino" value={`${num(m.sharpe)} / ${num(m.sortino)}`} hint="daily, annualised; noisy on short samples" />
            </StatGrid>
            {m.halted && <div className="panel p-3 text-sm text-accent-down">Account halted by the drawdown limit: {m.halted}</div>}

            <Panel title="Equity">
              <EquityChart curve={r.curve} />
            </Panel>
            <Panel title="Drawdown">
              <DrawdownChart curve={r.curve} limit={cfg?.risk?.max_account_drawdown_percent} />
            </Panel>

            <Panel title="Profit per basket vs maximum adverse excursion (MAE)" right={<span className="text-xs text-base-muted">Is it earning small profits while sitting through large open losses?</span>}>
              <div className="grid lg:grid-cols-[minmax(0,1fr)_320px] gap-5">
                <MaeScatter points={mm.scatter} />
                <div className="grid gap-3 content-start text-sm">
                  <div className="grid grid-cols-2 gap-3">
                    <StatCard label="Avg winning basket" value={money(mm.avg_winner_profit)} tone="up" />
                    <StatCard label="Avg MAE of winners" value={money(mm.avg_winner_mae)} tone="down" />
                    <StatCard label="Median profit ÷ MAE" value={num(mm.median_winner_profit_to_mae)} hint="winners; below 1 = held more risk than it earned" />
                    <StatCard label="Worst MAE" value={money(mm.worst_mae)} tone="down" hint={`${num(mm.worst_mae_in_avg_wins, 1)}× the average win`} />
                  </div>
                  <p className="text-base-muted leading-relaxed">
                    {num(mm.winners_with_mae_over_1x_profit_pct, 0)}% of winning baskets were at some point down more than they finally made;
                    {" "}{num(mm.winners_with_mae_over_3x_profit_pct, 0)}% were down more than 3× their profit. The largest losing basket
                    equals {num(mm.largest_loss_in_avg_wins, 1)} average wins.
                  </p>
                </div>
              </div>
            </Panel>

            <div className="grid lg:grid-cols-2 gap-5 items-start">
              <Panel title="Basket P&L distribution"><PnlHistogram dist={m.pnl_distribution} /></Panel>
              <Panel title="How baskets closed">
                <Table head={["Reason", "Baskets"]} align={["l", "r"]} rows={Object.entries(m.close_reasons).map(([k, v]) => [label(k), v])} />
                <div className="mt-4">
                  <Table head={["Positions in basket", "Baskets"]} align={["l", "r"]} rows={Object.entries(m.positions_histogram).map(([k, v]) => [k, v])} />
                </div>
              </Panel>
            </div>

            <Panel title="Performance by market regime at entry"><RegimeTable groups={m.by_regime} /></Panel>
            <Panel title="Performance by volatility regime at entry"><RegimeTable groups={m.by_vol_regime} /></Panel>

            <div className="grid lg:grid-cols-2 gap-5 items-start">
              <Panel title="Monthly performance">
                <Table head={["Month", "P&L", "Return"]} align={["l", "r", "r"]} rows={m.monthly.map((x) => [
                  x.month, <span className={tone(x.pnl) === "down" ? "text-accent-down" : "text-accent-up"}>{signedMoney(x.pnl)}</span>, pct(x.return_pct, 2, true)])} />
              </Panel>
              <Panel title="Exposure and risk events">
                <Table head={["Measure", "Value"]} align={["l", "r"]} rows={[
                  ["Max notional", money(m.max_notional, 0)],
                  ["Max effective leverage", `${num(m.max_effective_leverage)}×`],
                  ["Max margin use", pct(m.max_margin_usage_pct)],
                  ["Time in market", pct(m.time_in_market_pct, 1)],
                  ...Object.entries(m.risk_event_counts ?? {}).map(([k, v]) => [`Risk event: ${label(k)}`, String(v)]),
                ]} />
              </Panel>
            </div>

            <Panel title={`Baskets (${baskets.length})`} right={
              <span className="flex gap-2">
                <button className="text-sm text-base-muted disabled:opacity-40" disabled={page === 0} onClick={() => setPage(page - 1)}>← Prev</button>
                <button className="text-sm text-base-muted disabled:opacity-40" disabled={(page + 1) * PAGE >= baskets.length} onClick={() => setPage(page + 1)}>Next →</button>
              </span>}>
              <Table
                head={["Basket", "Dir", "Opened", "Bars", "Positions", "Lots", "Avg entry", "Exit", "P&L", "MAE", "MFE", "Regime", "Closed by"]}
                align={["l", "l", "l", "r", "r", "r", "r", "r", "r", "r", "r", "l", "l"]}
                rows={baskets.slice(page * PAGE, (page + 1) * PAGE).map((b) => [
                  b.uid, <Direction d={b.direction} />, when(b.opened_at), b.bars_held, b.positions, num(b.total_lots, 2), price(b.avg_entry), price(b.exit_price),
                  <span className={b.pnl < 0 ? "text-accent-down" : "text-accent-up"}>{signedMoney(b.pnl)}</span>, money(b.mae), money(b.mfe), <Regime r={b.regime} />, label(b.close_reason),
                ])}
              />
            </Panel>
          </>
        )}
      </Page>
    </>
  );
}
