import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createChart, CrosshairMode, IChartApi, ISeriesApi, LineStyle, SeriesMarker, Time, UTCTimestamp,
} from "lightweight-charts";
import { num } from "../components/format";
import { Empty, ErrorBox, Page, PageHeader, Panel, StatCard, StatGrid, Table } from "../components/ui";
import { Dict, get } from "../services/api";

const TFS = ["5m", "15m", "1h", "4h", "1d"];
const LAYERS: [string, string][] = [["structure", "BOS / CHoCH"], ["swings", "HH / HL / LH / LL"], ["sweeps", "Liquidity sweeps"],
  ["signals", "Strategy setups"], ["levels", "Support / resistance"], ["zones", "Supply / demand"], ["trendlines", "Trendlines"],
  ["plan", "Entry / stop / target"], ["robot", "SPARKZ trades"]];
const NAMES: Record<string, string> = {
  trend_following: "Trend following", market_structure: "Market structure", support_resistance: "Support & resistance",
  supply_demand: "Supply & demand", trendline_break: "Trendline break", sr_flip: "S/R flip", range_trading: "Range trading",
  liquidity_sweep: "Liquidity sweep", scalping_momentum: "Scalping momentum",
};
const C = { up: "#5cc488", down: "#ff7068", ink: "#f0eae8", muted: "#aa9898", line: "#4a2628", grid: "rgba(74,38,40,0.35)",
  entry: "#78bed2", warn: "#e0a64a", bg: "#120d0e" };
// lightweight-charts draws times as UTC: shift them so the axis shows this computer's local time
const SHIFT = -new Date().getTimezoneOffset() * 60;
const T = (t: number) => (t + SHIFT) as UTCTimestamp;

function loadLayers(): Record<string, boolean> {
  try { const v = JSON.parse(localStorage.getItem("sparkz-chart-layers") ?? "null"); if (v) return v; } catch { /* none */ }
  return Object.fromEntries(LAYERS.map(([k]) => [k, k !== "swings" && k !== "sweeps"]));
}

export default function ChartPage() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [robot, setRobot] = useState("XAUUSD");
  const [symbol, setSymbol] = useState(() => { try { return localStorage.getItem("sparkz-chart-symbol") ?? ""; } catch { return ""; } });
  const [tf, setTf] = useState(() => { try { return localStorage.getItem("sparkz-chart-tf") ?? "1h"; } catch { return "1h"; } });
  const [layers, setLayers] = useState<Record<string, boolean>>(loadLayers);
  const [only, setOnly] = useState(() => { try { return localStorage.getItem("sparkz-chart-only") ?? "top3"; } catch { return "top3"; } });
  const [data, setData] = useState<Dict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const extras = useRef<ISeriesApi<"Line">[]>([]);
  const fitted = useRef("");

  useEffect(() => {
    get("/api/chart/symbols").then((s) => {
      setSymbols(s.symbols); setRobot(s.robot_symbol);
      setSymbol((cur) => (cur && s.symbols.includes(cur) ? cur : s.robot_symbol));
    }).catch((e) => setError((e as Error).message));
  }, []);
  useEffect(() => { try { localStorage.setItem("sparkz-chart-layers", JSON.stringify(layers)); localStorage.setItem("sparkz-chart-only", only); } catch { /* private mode */ } }, [layers, only]);
  useEffect(() => { try { if (symbol) localStorage.setItem("sparkz-chart-symbol", symbol); localStorage.setItem("sparkz-chart-tf", tf); } catch { /* */ } }, [symbol, tf]);

  const load = useCallback(async () => {
    if (!symbol) return;
    try { const d = await get(`/api/chart?symbol=${encodeURIComponent(symbol)}&tf=${tf}&bars=300`); setData(d); setError(d.error ?? null); }
    catch (e) { setError((e as Error).message); }
  }, [symbol, tf]);
  useEffect(() => { setData(null); load(); const t = setInterval(load, 15_000); return () => clearInterval(t); }, [load]);

  // the chart object, once
  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, {
      autoSize: true,
      layout: { background: { color: C.bg }, textColor: C.muted, fontFamily: "'Source Sans 3', system-ui, sans-serif" },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: C.line },
      timeScale: { borderColor: C.line, timeVisible: true, secondsVisible: false, rightOffset: 8 },
    });
    candles.current = c.addCandlestickSeries({ upColor: C.up, downColor: C.down, borderUpColor: C.up, borderDownColor: C.down,
      wickUpColor: C.up, wickDownColor: C.down });
    chart.current = c;
    return () => { c.remove(); chart.current = null; candles.current = null; extras.current = []; };
  }, []);

  const plan = useMemo(() => planOf(data), [data]);
  const ranking: string[] = Object.keys(data?.analysis?.strategy_fit_pct ?? {});
  const shown = useMemo(() => {
    const sigs: Dict[] = data?.signals ?? [];
    if (only === "all") return sigs;
    const keep = only.startsWith("top") ? ranking.slice(0, Number(only.slice(3))) : [only];
    return sigs.filter((g) => keep.includes(g.strategy));
  }, [data, only, ranking.join()]);

  // draw candles and every layer
  useEffect(() => {
    const c = chart.current, s = candles.current;
    if (!c || !s) return;
    for (const x of extras.current) c.removeSeries(x);
    extras.current = [];
    for (const pl of (s as any).__lines ?? []) s.removePriceLine(pl);
    (s as any).__lines = [];
    if (!data?.candles?.length) { s.setData([]); s.setMarkers([]); return; }
    const digits = data.digits ?? 2;
    const fmt = { type: "price" as const, precision: digits, minMove: Math.pow(10, -digits) };
    s.applyOptions({ priceFormat: fmt });
    s.setData(data.candles.map((k: Dict) => ({ time: T(k.time), open: k.open, high: k.high, low: k.low, close: k.close })));
    const lastTime: number = data.candles[data.candles.length - 1].time;
    const segment = (pts: [number, number][], color: string, style = LineStyle.Solid, width: 1 | 2 = 1) => {
      const ln = c.addLineSeries({ color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false, priceFormat: fmt });
      const uniq = pts.filter((p, i) => i === 0 || p[0] > pts[i - 1][0]);
      if (uniq.length < 2) { c.removeSeries(ln); return; }
      ln.setData(uniq.map(([t, v]) => ({ time: T(t), value: v })));
      extras.current.push(ln);
    };
    const line = (price: number, color: string, title: string, style = LineStyle.Dotted, width: 1 | 2 = 1) =>
      (s as any).__lines.push(s.createPriceLine({ price, color, lineWidth: width, lineStyle: style, axisLabelVisible: true, title }));

    const markers: SeriesMarker<Time>[] = [];
    if (layers.structure) for (const e of (data.structure ?? []).slice(-25)) {
      const up = e.direction === "UP";
      markers.push({ time: T(e.time), position: up ? "aboveBar" : "belowBar", color: up ? C.up : C.down, shape: "square", size: 0.4,
        text: `${e.type}${up ? "↑" : "↓"}` });
      if (e.from_time && e.from_time < e.time) segment([[e.from_time, e.level], [e.time, e.level]], up ? C.up : C.down, LineStyle.Dashed);
    }
    if (layers.swings) for (const w of data.swings ?? [])
      markers.push({ time: T(w.time), position: w.kind === "high" ? "aboveBar" : "belowBar", color: C.muted, shape: "circle", size: 0.3, text: w.label });
    if (layers.sweeps) for (const w of data.sweeps ?? [])
      markers.push({ time: T(w.time), position: w.direction === "UP" ? "belowBar" : "aboveBar", color: C.warn, shape: "circle", size: 0.5, text: "Sweep" });
    if (layers.signals) for (const g of shown) {
      const buy = g.action === "BUY";
      markers.push({ time: T(g.time), position: buy ? "belowBar" : "aboveBar", color: buy ? C.up : C.down, shape: buy ? "arrowUp" : "arrowDown",
        text: g.short });
    }
    if (layers.robot && data.robot_trades?.length) {
      const times: number[] = data.candles.map((k: Dict) => k.time);
      const snap = (iso: string) => {                     // the candle a trade time falls in
        const t = Date.parse(iso) / 1000;
        let lo = 0, hi = times.length - 1;
        if (t < times[0]) return null;
        while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (times[mid] <= t) lo = mid; else hi = mid - 1; }
        return times[lo];
      };
      for (const tr of data.robot_trades) {
        const a = snap(tr.opened_at), b = snap(tr.closed_at);
        const buy = tr.direction === "BUY";
        if (a != null) markers.push({ time: T(a), position: buy ? "belowBar" : "aboveBar", color: C.entry, shape: buy ? "arrowUp" : "arrowDown", text: `SPARKZ ${tr.direction}` });
        if (b != null) markers.push({ time: T(b), position: "aboveBar", color: tr.pnl > 0 ? C.up : C.down, shape: "square", size: 0.6, text: `${tr.pnl > 0 ? "+" : ""}${tr.pnl.toFixed(2)}` });
        if (a != null && b != null && b > a && tr.entry != null && tr.exit != null) segment([[a, tr.entry], [b, tr.exit]], tr.pnl > 0 ? C.up : C.down, LineStyle.Dotted, 2);
      }
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    s.setMarkers(markers);

    if (layers.levels) for (const l of data.levels ?? []) line(l.price, C.muted, `S/R ×${l.touches}`);
    if (layers.zones) for (const z of data.zones ?? []) {
      const col = z.kind === "demand" ? C.up : C.down;
      segment([[z.from_time, z.high], [lastTime, z.high]], col);
      segment([[z.from_time, z.low], [lastTime, z.low]], col);
    }
    if (layers.trendlines) for (const tl of data.trendlines ?? [])
      segment(tl.points.map((p: Dict) => [p.time, p.price] as [number, number]), tl.kind === "support" ? C.up : C.down, LineStyle.Solid, 2);
    const ro = data.robot_open;
    if (layers.robot && ro?.average_entry != null) {
      line(ro.average_entry, C.entry, `SPARKZ ${ro.direction} ${ro.total_lots ?? ""}`, LineStyle.Solid, 2);
      if (ro.target_price != null) line(ro.target_price, C.up, "SPARKZ TARGET", LineStyle.Dashed);
      if (ro.stop_price != null) line(ro.stop_price, C.down, "SPARKZ LOSS LIMIT", LineStyle.Dashed);
    }
    if (layers.plan && plan) {
      line(plan.entry, C.entry, `${plan.action} ENTRY`, LineStyle.Solid, 2);
      line(plan.stop, C.down, "STOP LOSS", LineStyle.Solid, 2);
      line(plan.target, C.up, "TAKE PROFIT", LineStyle.Solid, 2);
    }
    const key = `${data.symbol}|${data.timeframe}`;
    if (fitted.current !== key) { const n = (box.current?.clientWidth ?? 1000) < 600 ? 60 : 150;
      c.timeScale().setVisibleLogicalRange({ from: Math.max(0, data.candles.length - n), to: data.candles.length + 5 }); fitted.current = key; }
  }, [data, layers, plan, shown]);

  const a: Dict = data?.analysis ?? {};
  const recent = shown.slice(-12).reverse();
  return (
    <>
      <PageHeader title="Chart" subtitle="Live candles with the strategy labels the assistant sees, and its entry, stop loss and take profit. Readings of past and present prices, not predictions." />
      <Page>
        <div className="flex flex-wrap items-center gap-2">
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="Market"
            className="bg-base-panel border border-base-border px-3 py-2 text-sm font-semibold">
            {(symbols.length ? symbols : [symbol || robot]).map((s) => <option key={s} value={s}>{s}{s === robot ? " (robot)" : ""}</option>)}
          </select>
          <div className="flex border border-base-border" role="group" aria-label="Timeframe">
            {TFS.map((t) => (
              <button key={t} onClick={() => setTf(t)}
                className={`px-3 py-2 text-sm ${tf === t ? "bg-accent-brand text-white font-semibold" : "text-base-muted hover:text-base-text"}`}>{t}</button>
            ))}
          </div>
          <span className="text-xs text-base-muted ml-auto">
            {data?.tick ? `bid ${num(data.tick.bid, data.digits)} · ask ${num(data.tick.ask, data.digits)} · ` : ""}
            {data?.spread != null ? `spread ${num(data.spread, data.digits)}` : ""} · updates every 15 s
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          <label className="flex items-center gap-1.5 text-base-muted">Setups from
            <select value={only} onChange={(e) => setOnly(e.target.value)} className="bg-base-panel border border-base-border px-2 py-1 text-xs text-base-text">
              <option value="top1">the #1 ranked strategy</option>
              <option value="top3">the top 3 ranked strategies</option>
              <option value="all">all strategies</option>
              {Object.entries(NAMES).map(([k, n]) => <option key={k} value={k}>{n} only</option>)}
            </select>
          </label>
          {LAYERS.map(([k, name]) => (
            <label key={k} className="flex items-center gap-1.5 cursor-pointer text-base-muted">
              <input type="checkbox" checked={!!layers[k]} onChange={(e) => setLayers({ ...layers, [k]: e.target.checked })} />{name}
            </label>
          ))}
        </div>
        {error && <ErrorBox error={error} />}
        <div className="panel p-0 overflow-hidden"><div ref={box} className="h-[420px] md:h-[560px] w-full" /></div>
        {symbols.length <= 1 && (
          <p className="text-xs text-base-muted">More markets: start the MT5 bridge with <code>--watch EURUSDm,GBPUSDm,USDJPYm</code> (your broker's names).</p>
        )}

        <div className="grid gap-5 lg:grid-cols-2">
          <Panel title={`Assistant on ${data?.symbol ?? symbol} ${tf}`} right={a.timeframes_read ? `reads ${a.timeframes_read.join(" · ")}` : undefined}>
            {a.error ? <ErrorBox error={a.error} /> : !a.action ? <Empty>Waiting for candles.</Empty> : (
              <div className="grid gap-3">
                <StatGrid cols={3}>
                  <StatCard label="Decision" value={a.action} tone={a.action === "BUY" ? "up" : a.action === "SELL" ? "down" : "neutral"} hint={`needs ${num(a.min_confidence_pct, 0)}%`} />
                  <StatCard label="Confidence" value={`${num(a.confidence_pct, 1)}%`} />
                  <StatCard label="Higher-timeframe bias" value={a.higher_timeframe_bias ?? "—"} />
                </StatGrid>
                <ul className="text-sm text-base-muted list-disc pl-5">{(a.reasons ?? []).map((r: string) => <li key={r}>{r}</li>)}</ul>
              </div>
            )}
          </Panel>
          <Panel title={plan ? `${plan.source}: ${plan.action} ${NAMES[plan.strategy] ?? plan.strategy}${plan.tf && plan.tf !== tf ? ` (${plan.tf} chart)` : ""}` : "Trade plan"}>
            {!plan ? <Empty>No setup on the latest candles: the assistant waits.</Empty> : (
              <div className="grid gap-3">
                <StatGrid cols={3}>
                  <StatCard label="Entry" value={num(plan.entry, data?.digits)} />
                  <StatCard label="Stop loss" value={num(plan.stop, data?.digits)} tone="down" hint={`risk ${num(Math.abs(plan.entry - plan.stop), data?.digits)}`} />
                  <StatCard label="Take profit" value={num(plan.target, data?.digits)} tone="up" hint={`reward ${num(plan.risk_reward, 2)} R`} />
                </StatGrid>
                {plan.history?.samples ? (
                  <p className="text-sm text-base-muted">Past record of this setup on this chart: target reached first in <b className="text-base-text">{num(plan.history.hit_rate_pct, 0)}%</b> of {plan.history.samples} earlier setups.</p>
                ) : null}
                {plan.source !== "Assistant plan" && <p className="text-xs text-accent-warn">The assistant's decision is {a.action}: this is the latest setup on the chart, not a trade signal.</p>}
              </div>
            )}
          </Panel>
        </div>

        <Panel title="Structure alerts on this market" right={data?.alert_timeframes?.length ? `new BOS / CHoCH on ${data.alert_timeframes.join(" · ")}` : "off"}>
          {!(data?.alerts ?? []).length ? <Empty>None since the server started. A new break of structure or change of character on a closed candle shows here, in the Event log and on Telegram.</Empty> : (
            <ul className="text-sm grid gap-1">{data!.alerts.map((x: Dict) => (
              <li key={x.timeframe + x.candle}><span className="text-base-muted">{new Date(x.time).toLocaleString()}</span> · <b className={x.event.endsWith("UP") ? "text-accent-up" : "text-accent-down"}>{x.message}</b></li>
            ))}</ul>
          )}
        </Panel>

        <div className="grid gap-5 lg:grid-cols-2">
          <Panel title="Strategy ranking (fit to the market now)">
            {!a.strategy_fit_pct ? <Empty>—</Empty> : (
              <div className="grid gap-1.5">
                {Object.entries(a.strategy_fit_pct as Record<string, number>).map(([k, v], i) => (
                  <div key={k} className="grid grid-cols-[1.5rem_10rem_1fr_3rem] items-center gap-2 text-sm">
                    <span className="text-base-muted">#{i + 1}</span><span className="truncate">{NAMES[k] ?? k}</span>
                    <span className="h-2 bg-base-tint"><span className="block h-2 bg-accent-brand" style={{ width: `${Math.min(100, v * 2)}%` }} /></span>
                    <span className="text-right tabular-nums">{num(v, 1)}%</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="Latest setups on this chart" right={only === "all" ? "all strategies" : only.startsWith("top") ? `top ${only.slice(3)} ranked` : NAMES[only]}>
            <Table head={["Time", "Strategy", "Side", "Entry", "Stop", "Target"]} align={["l", "l", "l", "r", "r", "r"]}
              rows={recent.map((g: Dict) => [new Date(g.time * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }),
                NAMES[g.strategy] ?? g.strategy, <span className={g.action === "BUY" ? "text-accent-up" : "text-accent-down"}>{g.action}</span>,
                num(g.entry, data?.digits), num(g.stop, data?.digits), num(g.target, data?.digits)])}
              empty="No setups on the visible candles." />
          </Panel>
        </div>
      </Page>
    </>
  );
}

/** The plan to draw: the assistant's own (when it decides BUY/SELL or has a plan), else the latest setup of the last 3 candles. */
function planOf(d: Dict | null): Dict | null {
  if (!d?.candles?.length) return null;
  const p = d.analysis?.plan;
  if (p && p.entry != null) return { ...p, source: d.analysis.action === "WAIT" ? "Setup (assistant waits)" : "Assistant plan" };
  const sigs: Dict[] = d.signals ?? [];
  const recentFrom = d.candles[Math.max(0, d.candles.length - 4)].time;
  const g = [...sigs].reverse().find((s) => s.time >= recentFrom);
  return g ? { ...g, source: "Latest setup" } : null;
}
