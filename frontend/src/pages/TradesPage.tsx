import { useAppState } from "../hooks/useAppState";
import { PageHeader } from "../components/ui";

export default function TradesPage() {
  const { lastBacktest } = useAppState();
  const trades = lastBacktest?.trades ?? [];

  return (
    <div>
      <PageHeader title="Trades" subtitle="Trade log from the most recent backtest run" />
      <div className="px-6 pb-8">
        {trades.length === 0 ? (
          <div className="panel p-6 text-sm text-base-muted">
            No trades yet — run a backtest from the Backtesting page first.
          </div>
        ) : (
          <div className="panel overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-base-border text-left text-base-muted text-xs uppercase tracking-wider">
                  <th className="px-4 py-2">Entry</th>
                  <th className="px-4 py-2">Exit</th>
                  <th className="px-4 py-2">Direction</th>
                  <th className="px-4 py-2">PnL</th>
                  <th className="px-4 py-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i} className="border-b border-base-border/50 font-mono-nums">
                    <td className="px-4 py-2 text-base-muted">{new Date(t.entry_time).toLocaleString()}</td>
                    <td className="px-4 py-2 text-base-muted">{new Date(t.exit_time).toLocaleString()}</td>
                    <td className={`px-4 py-2 ${t.direction === "BUY" ? "text-accent-up" : "text-accent-down"}`}>
                      {t.direction}
                    </td>
                    <td className={`px-4 py-2 ${t.pnl >= 0 ? "text-accent-up" : "text-accent-down"}`}>
                      {t.pnl >= 0 ? "+" : ""}
                      {t.pnl.toFixed(2)}
                    </td>
                    <td className="px-4 py-2 text-base-muted">{t.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
