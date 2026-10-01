import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import type { PaperEvaluation, PaperNorms, PaperRunSummary } from "../types/api";
import { DisclaimerNote, ErrorBanner, LoadingBlock, PageHeader, StatCard } from "../components/ui";

const money = (v: number) => v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (v: number) => `${v >= 0 ? "+" : ""}${money(v)}`;
const pct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
const tone = (v: number | null | undefined) => (v == null || v === 0 ? "neutral" : v > 0 ? "up" : "down");
const toneClass = (v: number | null | undefined) =>
  v == null || v === 0 ? "text-base-muted" : v > 0 ? "text-accent-up" : "text-accent-down";
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
const STRATEGY_LABELS: Record<string, string> = {
  baseline: "Baseline (buy & sell)",
  baseline_long_only: "Baseline, buy-only",
};

export default function PaperTradingPage() {
  const [runs, setRuns] = useState<PaperRunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .paperRuns()
      .then(setRuns)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  const totalStart = runs?.reduce((s, r) => s + r.starting_balance, 0) ?? 0;
  const totalEquity = runs?.reduce((s, r) => s + r.equity, 0) ?? 0;

  return (
    <div>
      <PageHeader
        title="Paper Trading"
        subtitle="Accounts updated once per candle by the paper-trade runner · positions marked at the latest price"
      />
      <div className="px-4 md:px-6 pb-8 space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={load}
            disabled={loading}
            className="btn-primary"
          >
            {loading ? "Refreshing…" : "Refresh prices"}
          </button>
          <span className="text-xs text-base-muted">
            Viewing never trades: positions only change when the runner processes a new closed candle.
          </span>
        </div>

        {error && <ErrorBanner message={error} />}
        {loading && !runs && <LoadingBlock label="Loading paper accounts…" />}

        {runs && runs.length === 0 && (
          <div className="panel p-6 text-sm text-base-muted space-y-2">
            <div>No paper accounts found in <code>data/paper/</code>.</div>
            <div>
              Create one with{" "}
              <code className="text-base-text">
                python -m app.cli paper-trade --account btc_daily_long --symbol BTC-USD --timeframe 1d --strategy
                baseline_long_only
              </code>
              , or pull the live state from the <code className="text-base-text">paper-trading</code> branch with{" "}
              <code className="text-base-text">git checkout origin/paper-trading -- data/paper</code>.
            </div>
          </div>
        )}

        {runs && runs.length > 1 && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard label="All Accounts · Equity" value={`$${money(totalEquity)}`} hint={`Started $${money(totalStart)}`} />
            <StatCard
              label="All Accounts · Return"
              value={pct(totalStart ? (totalEquity / totalStart - 1) * 100 : 0)}
              tone={tone(totalEquity - totalStart)}
            />
            <StatCard
              label="Open Positions"
              value={String(runs.reduce((s, r) => s + r.open_positions.length, 0))}
              hint={(() => {
                const n = runs.filter((r) => r.halted).length;
                return n ? `${n} account${n > 1 ? "s" : ""} halted` : undefined;
              })()}
            />
            <StatCard
              label="Closed Trades"
              value={String(runs.reduce((s, r) => s + r.closed_trades.length, 0))}
              hint={`${runs.reduce((s, r) => s + r.winning_trades, 0)} winning`}
            />
          </div>
        )}

        {runs?.map((r) => <AccountPanel key={r.account_name} run={r} />)}

        <DisclaimerNote>
          Paper trading only: simulated fills with the same spread, slippage and risk rules as the backtester. No real
          orders are placed. A few weeks of paper results are a small sample and do not guarantee future performance.
        </DisclaimerNote>
      </div>
    </div>
  );
}

const RESEARCH_BADGE: Record<string, string> = { tested: "badge-buy", thin: "badge-hold", control: "badge-sell" };

function AccountPanel({ run: r }: { run: PaperRunSummary }) {
  const unrealized = r.open_positions.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0);
  return (
    <div className="panel">
      <div className="panel-header flex flex-wrap items-center justify-between gap-2">
        <span>
          {r.account_name} · {r.symbol} · {r.timeframe} · {STRATEGY_LABELS[r.strategy] ?? r.strategy}
          {r.research && (
            <span className={`ml-2 badge ${RESEARCH_BADGE[r.research.group]}`} title={r.research.summary}>
              {r.research.label}
            </span>
          )}
          {r.halted && <span className="ml-2 badge badge-sell">HALTED</span>}
        </span>
        <span className="text-xs text-base-muted font-normal">Last candle processed: {when(r.last_processed)}</span>
      </div>
      <div className="p-4 space-y-4">
        {r.halted && (
          <ErrorBanner
            message={`Halted by the max-drawdown limit since ${r.halted}. Open positions still run to their stop or target, but no new trades will open until you resume it: python -m app.cli paper-trade --account ${r.account_name} --resume`}
          />
        )}
        {r.price_error && (
          <ErrorBanner message={`Latest price unavailable, so open positions aren't marked to market: ${r.price_error}`} />
        )}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <StatCard label="Equity" value={`$${money(r.equity)}`} hint={`Started $${money(r.starting_balance)}`} />
          <StatCard label="Return" value={pct(r.return_pct)} tone={tone(r.return_pct)} />
          <StatCard label="Unrealized P&L" value={signed(unrealized)} tone={tone(unrealized)} />
          <StatCard
            label="Latest Price"
            value={r.latest_price != null ? money(r.latest_price) : "—"}
            hint={r.latest_price_at ? `as of ${when(r.latest_price_at)}` : undefined}
          />
          <StatCard
            label="Closed Trades"
            value={String(r.closed_trades.length)}
            hint={r.closed_trades.length ? `${r.winning_trades} winning` : "None yet"}
          />
        </div>

        {r.research && (
          <p className="text-xs text-base-muted">
            Research: {r.research.summary} See {r.research.reports.map((f) => f.split("/").pop()).join(" and ")}.
          </p>
        )}

        {r.evaluation && <EvaluationSection evaluation={r.evaluation} accountName={r.account_name} />}

        {r.norms && <NormsSection norms={r.norms} />}

        <Section title="Open Position">
          {r.open_positions.length === 0 ? (
            <Empty>Flat: waiting for the next BUY signal.</Empty>
          ) : (
            <Table
              head={["Opened", "Side", "Size", "Entry", "Unrealized", "Stop", "Target"]}
              rows={r.open_positions.map((p) => [
                when(p.opened_at),
                <span className={p.direction === "BUY" ? "text-accent-up" : "text-accent-down"}>{p.direction}</span>,
                p.size.toFixed(6),
                money(p.entry_price),
                <span className={toneClass(p.unrealized_pnl)}>
                  {p.unrealized_pnl != null ? signed(p.unrealized_pnl) : "—"}
                </span>,
                <span>
                  {money(p.stop_price)}
                  {p.stop_distance_pct != null && (
                    <span className="text-base-muted"> ({pct(p.stop_distance_pct)})</span>
                  )}
                </span>,
                <span>
                  {money(p.target_price)}
                  {p.target_distance_pct != null && (
                    <span className="text-base-muted"> ({pct(p.target_distance_pct)})</span>
                  )}
                </span>,
              ])}
            />
          )}
        </Section>

        <Section title="Closed Trades">
          {r.closed_trades.length === 0 ? (
            <Empty>No closed trades yet.</Empty>
          ) : (
            <Table
              head={["Opened", "Closed", "Side", "Entry", "Exit", "P&L", "Reason"]}
              rows={r.closed_trades.map((t) => [
                when(t.opened_at),
                when(t.closed_at),
                t.direction,
                money(t.entry_price),
                money(t.exit_price),
                <span className={toneClass(t.pnl)}>{signed(t.pnl)}</span>,
                t.reason,
              ])}
            />
          )}
        </Section>

        {r.log.length > 0 && (
          <Section title="Activity">
            <ul className="text-xs font-mono-nums text-base-muted space-y-1">
              {r.log.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </Section>
        )}
      </div>
    </div>
  );
}

const STATUS_STYLE: Record<string, string> = {
  pass: "text-accent-up",
  fail: "text-accent-down",
  pending: "text-base-muted",
};

function EvaluationSection({ evaluation: e, accountName }: { evaluation: PaperEvaluation; accountName: string }) {
  const tone = e.verdict.startsWith("Failing")
    ? "text-accent-down"
    : e.verdict === "Passing"
      ? "text-accent-up"
      : "text-base-text";
  if (e.checks.length === 0) {
    return (
      <Section title="Paper vs Backtest">
        <Empty>
          No pass/fail targets yet. Set them once with{" "}
          <code className="text-base-text">python -m app.cli paper-trade --account {accountName} --set-targets</code>
        </Empty>
      </Section>
    );
  }
  const progress = Math.min(100, (e.closed_trades / e.min_trades) * 100);
  return (
    <Section title="Paper vs Backtest">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <span className={`text-sm font-semibold ${tone}`}>{e.verdict}</span>
        {e.targets_source && <span className="text-[11px] text-base-muted">Targets fixed from {e.targets_source}</span>}
        {e.targets_reset_reason && <span className="text-[11px] text-base-muted">Targets reset: {e.targets_reset_reason}</span>}
      </div>
      <div className="h-1.5 bg-base-bg rounded overflow-hidden border border-base-border mb-3" title="Closed trades toward the evaluation point">
        <div className="h-full bg-accent-brand" style={{ width: `${progress}%` }} />
      </div>
      <Table
        head={["Check", "Target", "Paper so far", "Status"]}
        rows={e.checks.map((c) => [
          c.name,
          <span className="text-base-muted">{c.target}</span>,
          c.actual,
          <span className={`uppercase text-xs font-semibold ${STATUS_STYLE[c.status]}`}>{c.status}</span>,
        ])}
      />
    </Section>
  );
}

// An early warning, separate from the verdict: flags a bad patch worse than anything the
// same backtest went through (app/paper/norms.py).
function NormsSection({ norms: n }: { norms: PaperNorms }) {
  const outside = n.verdict.startsWith("Outside");
  const notes = n.items.filter((i) => i.note);
  return (
    <Section title="Normal-losses check">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <span className={`text-sm font-semibold ${outside ? "text-accent-down" : "text-accent-up"}`}>{n.verdict}</span>
        <span className="text-[11px] text-base-muted">
          Compared with the worst of {n.backtest_trades.toLocaleString()} backtest trades over {n.backtest_days.toLocaleString()} days
        </span>
      </div>
      <Table
        head={["Measure", "Paper so far", "Backtest worst", "Status"]}
        rows={n.items.map((i) => [
          i.name,
          i.live,
          <span className="text-base-muted">{i.backtest}</span>,
          <span className={`uppercase text-xs font-semibold ${i.status === "outside" ? "text-accent-down" : "text-base-muted"}`}>
            {i.status === "outside" ? "outside range" : "normal"}
          </span>,
        ])}
      />
      {notes.length > 0 && (
        <ul className="mt-2 grid gap-0.5 text-xs text-base-muted">
          {notes.map((i) => (
            <li key={i.name}>
              {i.name}: {i.note}.
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-base-muted">
        An early warning, not a verdict: it doesn't change the pass/fail targets above.
      </p>
    </Section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider text-base-muted mb-2">{title}</div>
      {children}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="text-sm text-base-muted">{children}</div>;
}

function Table({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-base-border text-left text-base-muted text-xs uppercase tracking-wider">
            {head.map((h) => (
              <th key={h} className="px-3 py-2 whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, i) => (
            <tr key={i} className="border-b border-base-border/50 font-mono-nums">
              {cells.map((c, j) => (
                <td key={j} className="px-3 py-2 whitespace-nowrap">
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
