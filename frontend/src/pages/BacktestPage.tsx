import { useState } from "react";
import { Area, CartesianGrid, ComposedChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../services/api";
import { useAppState } from "../hooks/useAppState";
import { DisclaimerNote, ErrorBanner, LoadingBlock, PageHeader, StatCard, SymbolTimeframePicker } from "../components/ui";

export default function BacktestPage() {
  const { symbol, timeframe, lastBacktest, setLastBacktest } = useAppState();
  const [initialCapital, setInitialCapital] = useState(10000);
  const [riskPerTrade, setRiskPerTrade] = useState(1);
  const [stopMultiplier, setStopMultiplier] = useState(2);
  const [takeProfitR, setTakeProfitR] = useState(2);
  const [spread, setSpread] = useState(1.2);
  const [slippage, setSlippage] = useState(0.3);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = () => {
    setLoading(true);
    setError(null);
    api
      .runBacktest({
        symbol,
        timeframe,
        initial_capital: initialCapital,
        risk_per_trade: riskPerTrade / 100,
        stop_atr_multiplier: stopMultiplier,
        take_profit_r: takeProfitR,
        spread_pips: spread,
        slippage_pips: slippage,
        strategy: "baseline",
      })
      .then(setLastBacktest)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  const equityData = (lastBacktest?.equity_curve ?? []).map((p) => ({
    time: new Date(p.timestamp).toLocaleDateString(),
    equity: p.equity,
  }));

  return (
    <div>
      <PageHeader title="Backtesting" subtitle="Event-driven, cost-aware backtest with next-bar execution" />
      <div className="px-6 pb-6">
        <SymbolTimeframePicker />
      </div>

      <div className="px-6 pb-6 panel p-4">
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
          <Field label="Starting Capital ($)" value={initialCapital} onChange={setInitialCapital} />
          <Field label="Risk / Trade (%)" value={riskPerTrade} onChange={setRiskPerTrade} step={0.1} />
          <Field label="Stop × ATR" value={stopMultiplier} onChange={setStopMultiplier} step={0.5} />
          <Field label="Take Profit (R)" value={takeProfitR} onChange={setTakeProfitR} step={0.5} />
          <Field label="Spread (pips)" value={spread} onChange={setSpread} step={0.1} />
          <Field label="Slippage (pips)" value={slippage} onChange={setSlippage} step={0.1} />
        </div>
        <button
          onClick={run}
          disabled={loading}
          className="mt-4 bg-accent-brand text-white text-sm px-4 py-2 rounded hover:bg-blue-600 disabled:opacity-50"
        >
          {loading ? "Running backtest…" : "Run Backtest"}
        </button>
      </div>

      <div className="px-6 pb-8 space-y-4">
        {error && <ErrorBanner message={error} />}
        {loading && <LoadingBlock label="Simulating trades with costs, stops, and risk limits…" />}

        {!loading && lastBacktest && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <StatCard
                label="Total Return"
                value={`${lastBacktest.metrics.total_return_pct.toFixed(2)}%`}
                tone={lastBacktest.metrics.total_return_pct >= 0 ? "up" : "down"}
              />
              <StatCard label="Win Rate" value={`${lastBacktest.metrics.win_rate_pct.toFixed(1)}%`} />
              <StatCard
                label="Profit Factor"
                value={lastBacktest.metrics.profit_factor != null ? lastBacktest.metrics.profit_factor.toFixed(2) : "—"}
              />
              <StatCard label="Max Drawdown" value={`${lastBacktest.metrics.max_drawdown_pct.toFixed(2)}%`} tone="down" />
              <StatCard label="Sharpe" value={lastBacktest.metrics.sharpe_ratio?.toFixed(2) ?? "—"} />
              <StatCard label="Sortino" value={lastBacktest.metrics.sortino_ratio?.toFixed(2) ?? "—"} />
              <StatCard label="Trades" value={String(lastBacktest.metrics.total_trades)} />
              <StatCard
                label="vs. Buy & Hold"
                value={
                  lastBacktest.baseline_comparison.buy_and_hold_return_pct != null
                    ? `${(lastBacktest.baseline_comparison.buy_and_hold_return_pct as number).toFixed(2)}%`
                    : "—"
                }
                hint="Directional context only"
              />
            </div>

            <div className="panel">
              <div className="panel-header">Equity Curve</div>
              <div className="p-4 h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={equityData}>
                    <CartesianGrid stroke="#1c2532" strokeDasharray="3 3" />
                    <XAxis dataKey="time" stroke="#6e7681" fontSize={11} minTickGap={40} />
                    <YAxis stroke="#6e7681" fontSize={11} domain={["auto", "auto"]} />
                    <Tooltip contentStyle={{ background: "#0f1520", border: "1px solid #1c2532", fontSize: 12 }} />
                    <Area type="monotone" dataKey="equity" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.15} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>

            {lastBacktest.warnings.length > 0 && (
              <ErrorBanner message={lastBacktest.warnings.join(" ")} />
            )}
          </>
        )}

        <DisclaimerNote>
          Backtest results reflect historical simulation only, including spread, slippage, and risk limits as
          configured above. They do not guarantee future performance, and the baseline strategy shown here has not
          been auto-optimized against this data.
        </DisclaimerNote>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  step = 1,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
}) {
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wider text-base-muted">{label}</span>
      <input
        type="number"
        value={value}
        step={step}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="mt-1 w-full bg-base-bg border border-base-border rounded px-2 py-1.5 text-sm text-base-text font-mono-nums"
      />
    </label>
  );
}
