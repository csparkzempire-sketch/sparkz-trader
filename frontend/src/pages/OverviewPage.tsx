import { useEffect, useState } from "react";
import { api } from "../services/api";
import { useAppState } from "../hooks/useAppState";
import type { LatestPriceResponse, PaperAccountResponse } from "../types/api";
import {
  DisclaimerNote,
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  RegimeBadge,
  StatCard,
  SymbolTimeframePicker,
} from "../components/ui";

export default function OverviewPage() {
  const { symbol, timeframe, lastModelId, lastBacktest, paperAccountName } = useAppState();
  const [latest, setLatest] = useState<LatestPriceResponse | null>(null);
  const [paperAccount, setPaperAccount] = useState<PaperAccountResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getLatestPrice(symbol, timeframe)
      .then((data) => !cancelled && setLatest(data))
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));

    api
      .paperAccount(paperAccountName)
      .then((data) => !cancelled && setPaperAccount(data))
      .catch(() => !cancelled && setPaperAccount(null));

    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe, paperAccountName]);

  return (
    <div>
      <PageHeader title="Overview" subtitle={`${symbol} · ${timeframe} · research & paper-trading snapshot`} />
      <div className="px-6 pb-6 flex items-center justify-between">
        <SymbolTimeframePicker />
      </div>

      <div className="px-6 pb-8">
        {error && <ErrorBanner message={error} />}
        {loading && !error && <LoadingBlock label="Fetching latest market snapshot…" />}

        {!loading && !error && latest && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard label="Selected Asset" value={latest.symbol} />
            <StatCard label="Timeframe" value={latest.timeframe.toUpperCase()} />
            <StatCard label="Latest Price" value={latest.close.toFixed(5)} />
            <div className="panel p-4">
              <div className="stat-label">Market Regime</div>
              <div className="mt-2">
                <RegimeBadge regime={latest.regime} />
              </div>
            </div>

            <StatCard
              label="Paper Balance"
              value={paperAccount ? `$${paperAccount.balance.toFixed(2)}` : "—"}
              hint={paperAccount ? (paperAccount.is_active ? "Account active" : "Account stopped") : "No account started"}
            />
            <StatCard
              label="Open Positions"
              value={paperAccount ? String(paperAccount.open_positions) : "—"}
            />
            <StatCard
              label="Latest Backtest Return"
              value={lastBacktest ? `${lastBacktest.metrics.total_return_pct.toFixed(2)}%` : "—"}
              tone={lastBacktest ? (lastBacktest.metrics.total_return_pct >= 0 ? "up" : "down") : "neutral"}
              hint={lastBacktest ? `${lastBacktest.metrics.total_trades} trades` : "Run a backtest to populate this"}
            />
            <StatCard
              label="Latest Model"
              value={lastModelId ? lastModelId.split("_").slice(0, 2).join(" ") : "None trained yet"}
              hint={lastModelId ?? undefined}
            />
          </div>
        )}

        <DisclaimerNote>
          Signals and probabilities shown throughout SPARKZ TRADER are model outputs from historical data, not
          predictions of certainty. Paper trading only — no real broker connection exists in this build.
        </DisclaimerNote>
      </div>
    </div>
  );
}
