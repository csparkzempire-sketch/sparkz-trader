import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { chart } from "../theme";
import type { CandleRow, CurvePoint } from "../types/api";
import { money, num, pct, price } from "../components/format";

const day = (t: string) => new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const stamp = (t: string) => new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const axisProps = () => ({ stroke: chart().axis, tick: { fill: chart().axis, fontSize: 11 }, tickLine: false });

export function EquityChart({ curve, height = 260 }: { curve: { t: string; equity: number; balance?: number }[]; height?: number }) {
  const c = chart();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={curve} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <CartesianGrid stroke={c.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="t" tickFormatter={day} minTickGap={40} {...axisProps()} />
        <YAxis domain={["auto", "auto"]} tickFormatter={(v) => `$${Math.round(v).toLocaleString()}`} width={72} {...axisProps()} />
        <Tooltip contentStyle={c.tooltip} labelFormatter={stamp} formatter={(v: number, n: string) => [money(v), n]} />
        <Line type="monotone" dataKey="equity" name="Equity" stroke={c.series1} dot={false} strokeWidth={1.6} isAnimationActive={false} />
        {curve.length > 0 && curve[0].balance !== undefined && (
          <Line type="stepAfter" dataKey="balance" name="Balance" stroke={c.series2} dot={false} strokeWidth={1} strokeOpacity={0.6} isAnimationActive={false} />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function DrawdownChart({ curve, limit, height = 160 }: { curve: CurvePoint[]; limit?: number; height?: number }) {
  const c = chart();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={curve} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <CartesianGrid stroke={c.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="t" tickFormatter={day} minTickGap={40} {...axisProps()} />
        <YAxis tickFormatter={(v) => `${v}%`} width={72} {...axisProps()} />
        <Tooltip contentStyle={c.tooltip} labelFormatter={stamp} formatter={(v: number) => [pct(v), "Drawdown"]} />
        {limit !== undefined && <ReferenceLine y={-limit} stroke={c.warn} strokeDasharray="4 4" label={{ value: `limit −${limit}%`, fill: c.warn, fontSize: 11, position: "insideBottomRight" }} />}
        <Area type="monotone" dataKey="drawdown_pct" stroke={c.down} fill={c.down} fillOpacity={0.25} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function PnlHistogram({ dist, height = 200 }: { dist: { from: number; to: number; count: number }[]; height?: number }) {
  const c = chart();
  const data = dist.map((d) => ({ ...d, mid: (d.from + d.to) / 2 }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={c.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="mid" tickFormatter={(v) => `$${Math.round(v)}`} {...axisProps()} />
        <YAxis allowDecimals={false} width={36} {...axisProps()} />
        <Tooltip contentStyle={c.tooltip} labelFormatter={(_, p: any) => (p?.[0] ? `${money(p[0].payload.from)} to ${money(p[0].payload.to)}` : "")}
          formatter={(v: number) => [v, "Baskets"]} />
        <Bar dataKey="count" isAnimationActive={false}>
          {data.map((d, i) => <Cell key={i} fill={d.mid >= 0 ? c.up : c.down} fillOpacity={0.8} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** The critical grid chart: final profit of each basket against the worst open loss it sat through. */
export function MaeScatter({ points, height = 300 }: { points: { uid: string; pnl: number; mae: number; positions: number }[]; height?: number }) {
  const c = chart();
  const wins = points.filter((p) => p.pnl > 0);
  const losses = points.filter((p) => p.pnl <= 0);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 8, right: 16, bottom: 22, left: 8 }}>
        <CartesianGrid stroke={c.grid} strokeDasharray="2 4" />
        <XAxis type="number" dataKey="mae" name="MAE" tickFormatter={(v) => `$${Math.round(v)}`} {...axisProps()}
          label={{ value: "Worst open loss during the basket (MAE)", fill: c.axis, fontSize: 11, position: "insideBottom", offset: -10 }} />
        <YAxis type="number" dataKey="pnl" name="P&L" tickFormatter={(v) => `$${Math.round(v)}`} width={60} {...axisProps()} />
        <ZAxis type="number" dataKey="positions" range={[20, 90]} name="Positions" />
        <ReferenceLine y={0} stroke={c.axis} />
        <Tooltip contentStyle={c.tooltip} formatter={(v: number, n: string) => [n === "Positions" ? v : money(v), n]} />
        <Scatter name="Winning baskets" data={wins} fill={c.up} fillOpacity={0.7} isAnimationActive={false} />
        <Scatter name="Losing baskets" data={losses} fill={c.down} fillOpacity={0.7} isAnimationActive={false} />
        <Legend verticalAlign="top" height={28} wrapperStyle={{ fontSize: 12, color: c.axis }} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export function PriceChart({ rows, height = 320 }: { rows: CandleRow[]; height?: number }) {
  const c = chart();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <CartesianGrid stroke={c.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="timestamp" tickFormatter={stamp} minTickGap={60} {...axisProps()} />
        <YAxis domain={["auto", "auto"]} tickFormatter={(v) => price(v)} width={72} {...axisProps()} />
        <Tooltip contentStyle={c.tooltip} labelFormatter={stamp} formatter={(v: number, n: string) => [price(v), n]} />
        <Line dataKey="bb_upper" name="BB upper" stroke={c.axis} strokeDasharray="3 3" dot={false} strokeWidth={1} isAnimationActive={false} />
        <Line dataKey="bb_lower" name="BB lower" stroke={c.axis} strokeDasharray="3 3" dot={false} strokeWidth={1} isAnimationActive={false} />
        <Line dataKey="ema_trend" name="EMA 200" stroke={c.series3} dot={false} strokeWidth={1.2} isAnimationActive={false} />
        <Line dataKey="ema_slow" name="EMA 50" stroke={c.series4} dot={false} strokeWidth={1.2} isAnimationActive={false} />
        <Line dataKey="ema_fast" name="EMA 20" stroke={c.series1} dot={false} strokeWidth={1.2} isAnimationActive={false} />
        <Line dataKey="close" name="Close" stroke={c.text} dot={false} strokeWidth={1.4} isAnimationActive={false} />
        <Legend wrapperStyle={{ fontSize: 12, color: c.axis }} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function OscillatorChart({ rows, keys, refs = [], height = 140 }: { rows: CandleRow[]; keys: { key: keyof CandleRow; name: string; color?: string }[]; refs?: number[]; height?: number }) {
  const c = chart();
  const colors = [c.series1, c.series2, c.series3];
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <CartesianGrid stroke={c.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="timestamp" tickFormatter={stamp} minTickGap={60} {...axisProps()} />
        <YAxis width={72} tickFormatter={(v) => num(v, 1)} {...axisProps()} />
        <Tooltip contentStyle={c.tooltip} labelFormatter={stamp} formatter={(v: number, n: string) => [num(v, 2), n]} />
        {refs.map((r) => <ReferenceLine key={r} y={r} stroke={c.axis} strokeDasharray="3 3" />)}
        {keys.map((k, i) => (
          <Line key={String(k.key)} dataKey={k.key as string} name={k.name} stroke={k.color ?? colors[i % 3]} dot={false} strokeWidth={1.3} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function MultiEquity({ series, height = 300 }: { series: { name: string; curve: { t: string; equity: number }[] }[]; height?: number }) {
  const c = chart();
  const colors = [c.series1, c.series2, c.series3, c.series4, c.up, c.warn, c.axis];
  const byT = new Map<string, Record<string, any>>();
  series.forEach((s) => s.curve.forEach((p) => {
    const row = byT.get(p.t) ?? { t: p.t };
    row[s.name] = p.equity;
    byT.set(p.t, row);
  }));
  const data = [...byT.values()].sort((a, b) => (a.t < b.t ? -1 : 1));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <CartesianGrid stroke={c.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="t" tickFormatter={day} minTickGap={40} {...axisProps()} />
        <YAxis domain={["auto", "auto"]} tickFormatter={(v) => `$${Math.round(v).toLocaleString()}`} width={72} {...axisProps()} />
        <Tooltip contentStyle={c.tooltip} labelFormatter={stamp} formatter={(v: number, n: string) => [money(v), n]} />
        {series.map((s, i) => (
          <Line key={s.name} dataKey={s.name} stroke={colors[i % colors.length]} dot={false} strokeWidth={1.4} connectNulls isAnimationActive={false} />
        ))}
        <Legend wrapperStyle={{ fontSize: 12, color: c.axis }} />
      </LineChart>
    </ResponsiveContainer>
  );
}
