import { api } from "../services/api";
import { ErrorBox, KeyValues, Page, PageHeader, Panel, Table } from "../components/ui";
import { useAppState, useLoad } from "../components/state";
import { num, pct } from "../components/format";

function Gauge({ value, limit, fmt }: { value: number; limit: number; fmt: (v: number) => string }) {
  const share = limit > 0 ? Math.min(1, Math.abs(value) / limit) : 0;
  const cls = share >= 1 ? "bg-accent-down" : share >= 0.7 ? "bg-accent-warn" : "bg-accent-up";
  return (
    <div className="grid gap-1 min-w-[140px]">
      <div className="flex justify-between text-xs"><span>{fmt(value)}</span><span className="text-base-muted">of {fmt(limit)}</span></div>
      <div className="h-1.5 bg-base-border/60"><div className={`h-full ${cls}`} style={{ width: `${share * 100}%` }} /></div>
    </div>
  );
}

export default function RiskPage() {
  const limits = useLoad(() => api.riskLimits(), []);
  const status = useLoad(() => api.riskStatus(), []);
  const { backtestId } = useAppState();
  const bt = useLoad(() => (backtestId ? api.getBacktest(backtestId) : Promise.resolve(null)), [backtestId]);
  const l = limits.data;
  const m = bt.data?.metrics;
  return (
    <>
      <PageHeader title="Risk" subtitle="Risk management always has priority: a signal never overrides a refused position." />
      <Page>
        <ErrorBox error={limits.error || status.error} />
        <Panel title="Paper accounts · live risk status">
          <Table head={["Account", "Drawdown", "Exposure", "Margin use", "Positions", "Emergency status"]}
            rows={(status.data ?? []).map((s) => [s.account,
              <Gauge value={s.drawdown_pct} limit={s.max_drawdown_pct} fmt={(v) => pct(v, 1)} />,
              <Gauge value={s.exposure_leverage} limit={s.max_exposure_leverage} fmt={(v) => `${num(v, 1)}×`} />,
              <Gauge value={s.margin_usage_pct} limit={s.max_margin_usage_pct} fmt={(v) => pct(v, 1)} />,
              <Gauge value={s.positions} limit={s.max_positions} fmt={(v) => String(v)} />,
              <span className={s.halted ? "text-accent-down font-semibold" : "text-accent-up font-semibold"}>{s.emergency}</span>])}
            empty="No paper accounts." />
        </Panel>
        {l && (
          <div className="grid lg:grid-cols-2 gap-5">
            <Panel title="Configured limits (default)">
              <KeyValues items={[
                ["Max positions per basket", `${l.risk.max_positions} (hard ceiling ${l.hard_max_positions})`],
                ["Basket loss limit", `${l.stop.max_basket_loss_percent}% of equity (and ≤ risk per cycle ${pct(l.risk.risk_per_cycle * 100, 1)})`],
                ["Max daily loss", `${l.risk.max_daily_loss_percent}%: blocks new baskets and adds for the day`],
                ["Max account drawdown", `${l.risk.max_account_drawdown_percent}%: closes the basket and halts until resumed`],
                ["Max exposure", `${l.risk.max_exposure_leverage}× equity (notional)`],
                ["Max margin use", `${l.risk.max_margin_usage_percent}%`],
                ["Cooldown after a losing basket", `${l.risk.cooldown_bars_after_loss} bars`],
                ["Martingale allowed by default", l.martingale_allowed_by_default ? "yes" : "no"],
                ["Live trading", l.live_trading_enabled ? "ENABLED" : "not implemented (fail closed)"],
              ]} />
            </Panel>
            {m && (
              <Panel title={`Selected backtest #${backtestId}`}>
                <KeyValues items={[
                  ["Max drawdown", pct(m.max_drawdown_pct)],
                  ["Max effective leverage", `${num(m.max_effective_leverage, 2)}×`],
                  ["Max margin use", pct(m.max_margin_usage_pct)],
                  ["Baskets at max positions", `${m.baskets_at_max_positions} of ${m.baskets}`],
                  ["Largest basket loss", `$${num(m.largest_basket_loss)}`],
                  ...Object.entries(m.risk_event_counts ?? {}).map(([k, v]) => [`Risk event ${k.toLowerCase()}`, String(v)] as [string, string]),
                  ["Halted", m.halted ?? "no"],
                ]} />
              </Panel>
            )}
          </div>
        )}
      </Page>
    </>
  );
}
