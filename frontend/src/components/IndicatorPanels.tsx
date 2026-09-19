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

const tooltipStyle = { background: "#0f1520", border: "1px solid #1c2532", fontSize: 12 };

export function RsiPanel({ data }: { data: Datum[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#1c2532" strokeDasharray="3 3" />
        <XAxis dataKey="time" stroke="#6e7681" fontSize={10} minTickGap={60} />
        <YAxis stroke="#6e7681" fontSize={10} domain={[0, 100]} width={30} ticks={[0, 30, 50, 70, 100]} />
        <Tooltip contentStyle={tooltipStyle} />
        <ReferenceLine y={70} stroke="#e5484d" strokeDasharray="4 4" strokeOpacity={0.5} />
        <ReferenceLine y={30} stroke="#26a969" strokeDasharray="4 4" strokeOpacity={0.5} />
        <Line type="monotone" dataKey="rsi" stroke="#a78bfa" strokeWidth={1.25} dot={false} name="RSI" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function MacdPanel({ data }: { data: Datum[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#1c2532" strokeDasharray="3 3" />
        <XAxis dataKey="time" stroke="#6e7681" fontSize={10} minTickGap={60} />
        <YAxis stroke="#6e7681" fontSize={10} width={40} />
        <Tooltip contentStyle={tooltipStyle} />
        <ReferenceLine y={0} stroke="#6e7681" />
        <Bar dataKey="macd_hist" name="Histogram">
          {data.map((d, i) => (
            <Cell key={i} fill={(d.macd_hist as number) >= 0 ? "#26a969" : "#e5484d"} />
          ))}
        </Bar>
        <Line type="monotone" dataKey="macd" stroke="#3b82f6" strokeWidth={1.25} dot={false} name="MACD" />
        <Line type="monotone" dataKey="macd_signal" stroke="#f59e0b" strokeWidth={1.25} dot={false} name="Signal" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function AtrPanel({ data }: { data: Datum[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#1c2532" strokeDasharray="3 3" />
        <XAxis dataKey="time" stroke="#6e7681" fontSize={10} minTickGap={60} />
        <YAxis stroke="#6e7681" fontSize={10} width={50} domain={["auto", "auto"]} />
        <Tooltip contentStyle={tooltipStyle} />
        <Line type="monotone" dataKey="atr" stroke="#22d3ee" strokeWidth={1.25} dot={false} name="ATR" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
