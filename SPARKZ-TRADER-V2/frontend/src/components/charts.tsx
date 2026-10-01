import { Area, AreaChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Dict } from "../services/api";
import { money, price as fmtPrice, when } from "./format";

const AXIS = { stroke: "rgb(170 152 152)", fontSize: 11 };
const GRID = "rgb(74 38 40 / 0.5)";
const TIP = { contentStyle: { background: "rgb(18 13 14)", border: "1px solid rgb(74 38 40)", fontSize: 12 } };
const short = (iso: string) => new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

/** Closes of recent candles with the open basket's levels and entries drawn on top. */
export function PriceChart({ candles, basket, live }: { candles: Dict[]; basket?: Dict | null; live?: number | null }) {
  const data = candles.map((c) => ({ t: c.time, close: c.close, high: c.high, low: c.low }));
  if (live && data.length) data.push({ t: new Date().toISOString(), close: live, high: live, low: live });
  const levels: { y: number; label: string; color: string }[] = [];
  if (basket) {
    levels.push({ y: basket.average_entry, label: "avg entry", color: "rgb(240 234 232)" });
    levels.push({ y: basket.target_price, label: "target", color: "rgb(110 200 140)" });
    levels.push({ y: basket.stop_price, label: "loss limit", color: "rgb(255 99 99)" });
  }
  // Scale to the candles; a level far outside them (often the loss limit) is listed instead of squashing the chart.
  const cy = data.flatMap((d) => [d.high, d.low]).filter((v) => Number.isFinite(v));
  const span = cy.length ? Math.max(...cy) - Math.min(...cy) : 0;
  const near = (y: number) => !cy.length || (y > Math.min(...cy) - span && y < Math.max(...cy) + span);
  const far = levels.filter((l) => !near(l.y));
  const shownLevels = levels.filter((l) => near(l.y));
  const ys = [...cy, ...shownLevels.map((l) => l.y)];
  const pad = ys.length ? (Math.max(...ys) - Math.min(...ys)) * 0.05 : 1;
  return (
    <>
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="t" tickFormatter={short} {...AXIS} minTickGap={60} />
        <YAxis domain={ys.length ? [Math.min(...ys) - pad, Math.max(...ys) + pad] : ["auto", "auto"]} tickFormatter={(v) => fmtPrice(v)} {...AXIS} width={70} />
        <Tooltip {...TIP} labelFormatter={(v) => when(String(v))} formatter={(v: number) => fmtPrice(v)} />
        <Line type="monotone" dataKey="close" stroke="rgb(255 99 99)" dot={false} strokeWidth={1.6} isAnimationActive={false} />
        {shownLevels.map((l) => (
          <ReferenceLine key={l.label} y={l.y} stroke={l.color} strokeDasharray="4 3" label={{ value: l.label, fill: l.color, fontSize: 11, position: "insideTopLeft" }} />
        ))}
        {basket?.entries?.map((e: Dict) => (
          <ReferenceLine key={e.seq} y={e.entry_price} stroke="rgb(214 160 90 / 0.6)" strokeDasharray="1 3" />
        ))}
      </LineChart>
    </ResponsiveContainer>
    {far.length > 0 && <p className="text-[11px] text-base-muted mt-1">Off chart: {far.map((l) => `${l.label} ${fmtPrice(l.y)}`).join(" · ")}</p>}
    </>
  );
}

export function EquityChart({ points, height = 220, initial }: { points: Dict[]; height?: number; initial?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="time" tickFormatter={short} {...AXIS} minTickGap={60} />
        <YAxis domain={["auto", "auto"]} tickFormatter={(v) => money(v, 0)} {...AXIS} width={80} />
        <Tooltip {...TIP} labelFormatter={(v) => when(String(v))} formatter={(v: number) => money(v)} />
        {initial && <ReferenceLine y={initial} stroke="rgb(170 152 152)" strokeDasharray="3 3" />}
        <Area type="monotone" dataKey="equity" stroke="rgb(255 99 99)" fill="rgb(255 99 99 / 0.15)" isAnimationActive={false} />
        <Line type="monotone" dataKey="balance" stroke="rgb(240 234 232)" dot={false} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Generic multi-line chart, e.g. the critical failure path. */
export function Lines({ data, x, series, height = 220, fmt = (v: number) => String(v) }: {
  data: Dict[]; x: string; series: { key: string; color: string; name?: string }[]; height?: number; fmt?: (v: number) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey={x} {...AXIS} />
        <YAxis {...AXIS} width={80} tickFormatter={fmt} />
        <Tooltip {...TIP} formatter={(v: number) => fmt(v)} />
        <ReferenceLine y={0} stroke="rgb(170 152 152)" />
        {series.map((s) => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.name ?? s.key} stroke={s.color} dot={false} isAnimationActive={false} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
