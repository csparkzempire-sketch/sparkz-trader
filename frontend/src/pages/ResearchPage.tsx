import { useEffect, useState } from "react";
import { api } from "../services/api";
import type { ResearchMarketRow, ResearchResults, ResearchSensitivity } from "../types/api";
import { DisclaimerNote, ErrorBanner, LoadingBlock, PageHeader } from "../components/ui";

const pct = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
const toneClass = (v: number | null | undefined) =>
  v == null || v === 0 ? "text-base-muted" : v > 0 ? "text-accent-up" : "text-accent-down";
const TF_LABEL: Record<string, string> = { "1h": "Hourly", "1d": "Daily" };
const ACCOUNT_LABEL = (a: ResearchSensitivity) =>
  `${a.symbol === "GC=F" ? "Gold" : a.symbol.replace("=X", "").replace("-USD", "")} · ${TF_LABEL[a.timeframe]} · ${
    a.side === "both" ? "Buy & sell" : "Buy-only"
  }`;

export default function ResearchPage() {
  const [data, setData] = useState<ResearchResults | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getResearch().then(setData).catch((e) => setError(e.message));
  }, []);

  return (
    <div>
      <PageHeader
        title="Research"
        subtitle="How the baseline strategy tested on each market, and whether the result depends on its exact settings"
      />
      <div className="px-4 md:px-6 pb-8 space-y-6">
        {error && <ErrorBanner message={error} />}
        {!data && !error && <LoadingBlock label="Loading research results…" />}
        {data && (
          <>
            <MarketsSection rows={data.markets} traded={data.sensitivity} />
            <SensitivitySection rows={data.sensitivity} />
            <DisclaimerNote>
              Results generated {new Date(data.generated_at).toLocaleString()}. {data.note} Backtests are simulations
              on past prices and do not guarantee future results. Rerun with{" "}
              <code className="text-base-text">python -m app.cli research</code>.
            </DisclaimerNote>
          </>
        )}
      </div>
    </div>
  );
}

function MarketsSection({ rows, traded }: { rows: ResearchMarketRow[]; traded: ResearchSensitivity[] }) {
  const groups = new Map<string, ResearchMarketRow[]>();
  for (const r of rows) {
    const k = `${r.symbol}|${r.timeframe}`;
    groups.set(k, [...(groups.get(k) ?? []), r]);
  }
  const isTraded = (symbol: string, tf: string) => traded.some((t) => t.symbol === symbol && t.timeframe === tf);

  return (
    <div className="panel">
      <div className="panel-header">Markets tested · full-period backtests</div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm whitespace-nowrap">
          <thead>
            <tr className="border-b border-base-border text-left text-base-muted text-xs uppercase tracking-wider">
              <th className="px-3 py-2">Market</th>
              <th className="px-3 py-2">Period</th>
              <th className="px-3 py-2 text-right">Buy-only</th>
              <th className="px-3 py-2 text-right">Buy & sell</th>
              <th className="px-3 py-2 text-right">Sell-only</th>
              <th className="px-3 py-2 text-right">Just holding</th>
              <th className="px-3 py-2 text-right">Profitable periods</th>
              <th className="px-3 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {[...groups.values()].map((g) => {
              const by = Object.fromEntries(g.map((r) => [r.side, r])) as Record<string, ResearchMarketRow>;
              const first = g[0];
              const tradedHere = isTraded(first.symbol, first.timeframe);
              const failed = by.buy.full.return_pct < 0 && by.both.full.return_pct < 0;
              return (
                <tr key={`${first.symbol}${first.timeframe}`} className="border-b border-base-border/50 font-mono-nums">
                  <td className="px-3 py-2 font-sans">
                    {first.market} <span className="text-base-muted">· {TF_LABEL[first.timeframe]}</span>
                  </td>
                  <td className="px-3 py-2 text-base-muted text-xs">
                    {first.start} → {first.end}
                  </td>
                  {(["buy", "both", "sell"] as const).map((side) => (
                    <td key={side} className="px-3 py-2 text-right">
                      <span className={toneClass(by[side].full.return_pct)}>{pct(by[side].full.return_pct, 0)}</span>
                      <span className="block text-[11px] text-base-muted">
                        PF {by[side].full.profit_factor?.toFixed(2) ?? "—"} · {by[side].full.trades} trades
                      </span>
                    </td>
                  ))}
                  <td className={`px-3 py-2 text-right ${toneClass(first.hold_pct)}`}>{pct(first.hold_pct, 0)}</td>
                  <td className="px-3 py-2 text-right">
                    {by.buy.periods.profitable}/{by.buy.periods.count}
                    <span className="block text-[11px] text-base-muted">buy-only, per {by.buy.periods.kind}</span>
                  </td>
                  <td className="px-3 py-2">
                    {tradedHere ? (
                      <span className="badge badge-buy">Paper trading</span>
                    ) : failed ? (
                      <span className="badge badge-sell">Rejected</span>
                    ) : (
                      <span className="badge badge-hold">Not traded</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="px-4 py-3 text-[11px] text-base-muted border-t border-base-border">
        PF = profit factor (gross profit ÷ gross loss). Profitable periods counts {`six-week windows (hourly)`} or calendar
        years (daily) in which buy-only made money; those counts shift noticeably with where the windows start, so read
        the full-period results first.
      </p>
    </div>
  );
}

function SensitivitySection({ rows }: { rows: ResearchSensitivity[] }) {
  const lo = Math.min(0, ...rows.map((r) => r.min_pct));
  const hi = Math.max(...rows.map((r) => r.max_pct));
  const x = (v: number) => ((v - lo) / (hi - lo || 1)) * 100;
  const allProfitable = rows.every((r) => r.profitable === r.count);

  return (
    <div className="panel">
      <div className="panel-header">Settings sensitivity · one setting changed at a time</div>
      <div className="p-4 space-y-3">
        <p className="text-sm text-base-muted">
          Each paper-account setup was rerun with the fast and slow moving averages, RSI threshold, stop distance and
          profit target moved one at a time ({rows[0]?.count ?? 0} variations each).{" "}
          {allProfitable
            ? "Every variation made money, so the result does not hinge on the exact settings."
            : "Some variations lost money; see the accounts below."}{" "}
          The bar spans the worst to best variation; the dot marks the current settings.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm whitespace-nowrap">
            <thead>
              <tr className="border-b border-base-border text-left text-base-muted text-xs uppercase tracking-wider">
                <th className="px-3 py-2">Paper account setup</th>
                <th className="px-3 py-2 text-right">Current settings</th>
                <th className="px-3 py-2 text-right">Profitable</th>
                <th className="px-3 py-2 min-w-[220px]">Range across variations</th>
                <th className="px-3 py-2 text-right">Rank</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.account} className="border-b border-base-border/50 font-mono-nums">
                  <td className="px-3 py-2 font-sans">{ACCOUNT_LABEL(r)}</td>
                  <td className={`px-3 py-2 text-right ${toneClass(r.default.return_pct)}`}>
                    {pct(r.default.return_pct, 0)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {r.profitable}/{r.count}
                  </td>
                  <td className="px-3 py-2">
                    <div className="relative h-4" title={`${pct(r.min_pct, 0)} to ${pct(r.max_pct, 0)}`}>
                      <div className="absolute top-1/2 h-px w-full bg-base-border" />
                      <div
                        className="absolute top-1/2 -translate-y-1/2 h-2 rounded-sm bg-accent-up/30"
                        style={{ left: `${x(r.min_pct)}%`, width: `${x(r.max_pct) - x(r.min_pct)}%` }}
                      />
                      <div
                        className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 h-3 w-3 rounded-full bg-accent-brand border-2 border-base-panel"
                        style={{ left: `${x(r.default.return_pct)}%` }}
                      />
                    </div>
                    <div className="flex justify-between text-[10px] text-base-muted">
                      <span>{pct(r.min_pct, 0)}</span>
                      <span>{pct(r.max_pct, 0)}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right text-base-muted">
                    {r.default_rank} of {r.count + 1}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-[11px] text-base-muted">
          Rank 1 is the best of the {(rows[0]?.count ?? 0) + 1} settings tried. A mid-table rank means the current
          settings were not picked because they happened to do best on this data. All results are on the same period
          of history, so this shows the strategy is not fragile, not that it will keep working.
        </p>
      </div>
    </div>
  );
}
