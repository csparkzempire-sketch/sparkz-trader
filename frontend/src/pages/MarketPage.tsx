import { useEffect, useMemo, useState } from "react";
import { api } from "../services/api";
import { useAppState } from "../hooks/useAppState";
import type { Candle } from "../types/api";
import { ErrorBanner, LoadingBlock, PageHeader, RegimeBadge, SymbolTimeframePicker } from "../components/ui";
import CandlestickChart from "../components/CandlestickChart";
import { AtrPanel, MacdPanel, RsiPanel } from "../components/IndicatorPanels";

function formatTime(ts: string) {
  const d = new Date(ts);
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:00`;
}

export default function MarketPage() {
  const { symbol, timeframe } = useAppState();
  const [candles, setCandles] = useState<Candle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getMarketData(symbol, timeframe, 200)
      .then((data) => !cancelled && setCandles(data.candles))
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe]);

  const chartData = useMemo(
    () =>
      candles.map((c) => ({
        time: formatTime(c.timestamp),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        ema_20: c.ema_20,
        ema_50: c.ema_50,
        rsi: c.rsi,
        macd: c.macd,
        macd_signal: c.macd_signal,
        macd_hist: c.macd_hist,
        atr: c.atr,
      })),
    [candles]
  );

  const latestRegime = candles.length > 0 ? candles[candles.length - 1].regime : null;

  return (
    <div>
      <PageHeader title="Market" subtitle="Candlesticks, EMAs, RSI, MACD, and ATR for the selected instrument" />
      <div className="px-6 pb-6 flex items-center justify-between">
        <SymbolTimeframePicker />
        <div className="flex items-center gap-2 text-xs text-base-muted">
          <span>Regime:</span>
          <RegimeBadge regime={latestRegime} />
        </div>
      </div>

      <div className="px-6 pb-8 space-y-3">
        {error && <ErrorBanner message={error} />}
        {loading && !error && <LoadingBlock label="Downloading, validating, and computing indicators…" />}

        {!loading && !error && candles.length > 0 && (
          <>
            <div className="panel">
              <div className="panel-header flex items-center justify-between">
                <span>Price · EMA 20 (blue) · EMA 50 (amber)</span>
                <span className="text-base-muted normal-case tracking-normal">
                  {candles.length} candles · {symbol} · {timeframe}
                </span>
              </div>
              <div className="p-3 h-96">
                <CandlestickChart data={chartData} />
              </div>
            </div>

            <div className="panel">
              <div className="panel-header">RSI (14)</div>
              <div className="p-3 h-40">
                <RsiPanel data={chartData} />
              </div>
            </div>

            <div className="panel">
              <div className="panel-header">MACD</div>
              <div className="p-3 h-40">
                <MacdPanel data={chartData} />
              </div>
            </div>

            <div className="panel">
              <div className="panel-header">ATR (14)</div>
              <div className="p-3 h-32">
                <AtrPanel data={chartData} />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
