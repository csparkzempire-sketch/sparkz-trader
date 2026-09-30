import { chart } from "../theme";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface Datum {
  time: string;
  [key: string]: string | number | null;
}



export function RsiPanel({ data }: { data: Datum[] }) {
  const c = chart();
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={c.grid} strokeDasharray="3 3" />
        <XAxis dataKey="time" stroke={c.axis} fontSize={10} minTickGap={60} />
        <YAxis stroke={c.axis} fontSize={10} domain={[0, 100]} width={30} ticks={[0, 30, 50, 70, 100]} />
        <Tooltip contentStyle={c.tooltip} />
        <ReferenceLine y={70} stroke={c.down} strokeDasharray="4 4" strokeOpacity={0.5} />
        <ReferenceLine y={30} stroke={c.up} strokeDasharray="4 4" strokeOpacity={0.5} />
        <Line type="monotone" dataKey="rsi" stroke={c.series3} strokeWidth={1.25} dot={false} name="RSI" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function MacdPanel({ data }: { data: Datum[] }) {
  const c = chart();
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={c.grid} strokeDasharray="3 3" />
        <XAxis dataKey="time" stroke={c.axis} fontSize={10} minTickGap={60} />
        <YAxis stroke={c.axis} fontSize={10} width={40} />
        <Tooltip contentStyle={c.tooltip} />
        <ReferenceLine y={0} stroke={c.axis} />
        <Bar dataKey="macd_hist" name="Histogram">
          {data.map((d, i) => (
            <Cell key={i} fill={(d.macd_hist as number) >= 0 ? c.up : c.down} />
          ))}
        </Bar>
        <Line type="monotone" dataKey="macd" stroke={c.series1} strokeWidth={1.25} dot={false} name="MACD" />
        <Line type="monotone" dataKey="macd_signal" stroke={c.series2} strokeWidth={1.25} dot={false} name="Signal" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function AtrPanel({ data }: { data: Datum[] }) {
  const c = chart();
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={c.grid} strokeDasharray="3 3" />
        <XAxis dataKey="time" stroke={c.axis} fontSize={10} minTickGap={60} />
        <YAxis stroke={c.axis} fontSize={10} width={50} domain={["auto", "auto"]} />
        <Tooltip contentStyle={c.tooltip} />
        <Line type="monotone" dataKey="atr" stroke={c.series4} strokeWidth={1.25} dot={false} name="ATR" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
