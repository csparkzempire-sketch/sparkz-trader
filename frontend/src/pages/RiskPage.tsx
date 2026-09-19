import { useAppState } from "../hooks/useAppState";
import { PageHeader, StatCard } from "../components/ui";

export default function RiskPage() {
  const { lastBacktest } = useAppState();
  const m = lastBacktest?.metrics;

  return (
    <div>
      <PageHeader title="Risk" subtitle="Exposure and drawdown from the most recent backtest configuration" />
      <div className="px-6 pb-8">
        {!m ? (
          <div className="panel p-6 text-sm text-base-muted">
            No backtest run yet — risk figures populate after a backtest completes.
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard label="Current Exposure" value={`${m.exposure_pct.toFixed(1)}%`} hint="Approx. bar-time in a position" />
            <StatCard label="Max Drawdown" value={`${m.max_drawdown_pct.toFixed(2)}%`} tone="down" />
            <StatCard label="Turnover" value={m.turnover.toFixed(2)} hint="Notional traded / initial capital" />
            <StatCard label="Open Positions Allowed" value="1" hint="max_simultaneous_positions (config)" />
            <StatCard label="Win Rate" value={`${m.win_rate_pct.toFixed(1)}%`} />
            <StatCard label="Avg Win / Avg Loss" value={`${m.average_win.toFixed(0)} / ${m.average_loss.toFixed(0)}`} />
            <StatCard label="Expectancy / Trade" value={m.expectancy.toFixed(2)} />
            <StatCard
              label="Risk Status"
              value={m.max_drawdown_pct <= -20 ? "ELEVATED" : "NORMAL"}
              tone={m.max_drawdown_pct <= -20 ? "down" : "neutral"}
              hint="Backtest-derived, not a live account status"
            />
          </div>
        )}
        <p className="text-[11px] text-base-muted mt-4 leading-relaxed">
          Risk limits (max drawdown, max daily loss, max exposure, cooldown) are enforced during simulation by the
          risk engine — a rejected trade simply doesn't appear in the trade log. These figures describe the backtest
          that just ran, not a live paper or real account.
        </p>
      </div>
    </div>
  );
}
