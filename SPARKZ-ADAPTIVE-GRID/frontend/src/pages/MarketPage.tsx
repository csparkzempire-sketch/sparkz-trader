import { useState } from "react";
import { api } from "../services/api";
import { Button, Direction, ErrorBox, Field, inputCls, KeyValues, Loading, Page, PageHeader, Panel, Regime, StatCard, StatGrid, Table } from "../components/ui";
import { SYMBOLS, TIMEFRAMES, useAppState, useLoad } from "../components/state";
import { num, pct, price, when } from "../components/format";
import { OscillatorChart, PriceChart } from "../charts/Charts";

export default function MarketPage() {
  const { symbol, setSymbol, timeframe, setTimeframe } = useAppState();
  const candles = useLoad(() => api.candles(symbol, timeframe, 300), [symbol, timeframe]);
  const analysis = useLoad(() => api.analysis(symbol, timeframe), [symbol, timeframe]);
  const inv = useLoad(() => api.inventory(), []);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function refresh() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.download(symbol, timeframe);
      setMsg(`Stored ${r.rows_out} bars from ${r.source}${r.note ? `. ${r.note}` : ""}`);
      candles.reload();
      analysis.reload();
      inv.reload();
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  const a = analysis.data;
  const f = a?.features ?? {};
  return (
    <>
      <PageHeader title="Market" subtitle="Stored candles with the analysis the entry engine uses. Signals are evaluated on closed bars only."
        right={
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Symbol"><select className={inputCls} value={symbol} onChange={(e) => setSymbol(e.target.value)}>{SYMBOLS.map((s) => <option key={s}>{s}</option>)}</select></Field>
            <Field label="Timeframe"><select className={inputCls} value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>{TIMEFRAMES.map((s) => <option key={s}>{s}</option>)}</select></Field>
            <Button onClick={refresh} disabled={busy}>{busy ? "Downloading…" : "Download latest"}</Button>
          </div>
        } />
      <Page>
        {msg && <div className="panel p-3 text-sm text-base-muted">{msg}</div>}
        <ErrorBox error={candles.error} />
        {a && (
          <StatGrid cols={5}>
            <StatCard label="Last closed bar" value={price(f.close)} hint={when(a.timestamp)} />
            <div className="panel p-3 md:p-4"><div className="stat-label">Regime</div><div className="mt-2"><Regime r={a.regime} /></div><div className="mt-1"><Regime r={a.vol_regime} /></div></div>
            <div className="panel p-3 md:p-4"><div className="stat-label">Entry signal</div><div className="mt-2"><Direction d={a.signal} /></div><div className="text-[11px] text-base-muted mt-1">{a.reasons.join("; ")}</div></div>
            <StatCard label="ATR (14)" value={price(f.atr)} hint={`${num(f.atr_pct, 3)}% of price`} />
            <StatCard label="ADX (trend strength)" value={num(f.adx, 1)} hint={`+DI ${num(f.plus_di, 1)} / −DI ${num(f.minus_di, 1)}`} />
          </StatGrid>
        )}
        <Panel title={`${symbol} ${timeframe} · price, EMA 20/50/200, Bollinger bands`}>
          {candles.loading ? <Loading /> : candles.data && <PriceChart rows={candles.data} />}
        </Panel>
        {candles.data && (
          <div className="grid lg:grid-cols-2 gap-5">
            <Panel title="RSI (14)"><OscillatorChart rows={candles.data} keys={[{ key: "rsi", name: "RSI" }]} refs={[30, 50, 70]} /></Panel>
            <Panel title="MACD (12/26/9)"><OscillatorChart rows={candles.data} keys={[{ key: "macd", name: "MACD" }, { key: "macd_signal", name: "Signal" }]} refs={[0]} /></Panel>
            <Panel title="ADX (14)"><OscillatorChart rows={candles.data} keys={[{ key: "adx", name: "ADX" }]} refs={[18, 20]} /></Panel>
            <Panel title="Analysis detail">
              <KeyValues items={[
                ["EMA 20 / 50 / 200", `${price(f.ema_fast)} / ${price(f.ema_slow)} / ${price(f.ema_trend)}`],
                ["Distance from EMA 20", `${num(f.dist_ema_fast_atr)} ATR`],
                ["RSI (14)", num(f.rsi, 1)],
                ["MACD histogram", num(f.macd_hist, 3)],
                ["Bollinger width", pct((f.bb_width ?? 0) * 100, 2)],
                ["ATR% percentile (trailing)", pct((f.vol_percentile ?? 0) * 100, 0)],
                ["Return, last 20 bars", pct((f.ret_recent ?? 0) * 100, 2, true)],
              ]} />
            </Panel>
          </div>
        )}
        <Panel title="Stored history">
          <Table head={["Symbol", "Timeframe", "Bars", "From", "To", "Source"]} align={["l", "l", "r", "l", "l", "l"]}
            rows={(inv.data ?? []).map((r) => [r.symbol, r.timeframe, r.bars.toLocaleString(), when(r.first), when(r.last), r.source])} />
          <p className="text-xs text-base-muted mt-3">Yahoo keeps about 60 days of 15-minute bars; downloads are added to the store, so history grows over time. XAUUSD from Yahoo is COMEX gold futures (GC=F), a proxy for spot. Import broker CSVs (MT5 format) for longer or spot history: <code>python -m app.cli import-csv FILE --symbol XAUUSD --timeframe 15m --tz Etc/GMT-2</code>.</p>
        </Panel>
      </Page>
    </>
  );
}
