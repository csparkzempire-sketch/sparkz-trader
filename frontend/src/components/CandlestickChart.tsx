import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

interface CandleDatum {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  ema_20: number | null;
  ema_50: number | null;
}

/**
 * Renders a single candlestick body + wick.
 *
 * Recharts has no native candlestick type, so we use its "range bar" trick:
 * the Bar's dataKey is a function returning [low, high], which makes
 * recharts lay out the bar's `y`/`height` to span exactly that price range
 * in pixel space (y = pixel position of `high`, y+height = pixel position
 * of `low`). Inside this custom shape we linearly interpolate open/close
 * within that same [y, y+height] span to draw the body, and draw the wick
 * across the full bar height.
 */
function CandleShape(props: any) {
  const { x, y, width, height, payload } = props;
  const { open, close, high, low } = payload as CandleDatum;
  const isUp = close >= open;
  const color = isUp ? "#26a969" : "#e5484d";

  const priceToY = (price: number) => {
    if (high === low) return y + height / 2;
    return y + ((high - price) / (high - low)) * height;
  };

  const yOpen = priceToY(open);
  const yClose = priceToY(close);
  const bodyTop = Math.min(yOpen, yClose);
  const bodyHeight = Math.max(1, Math.abs(yClose - yOpen));
  const wickX = x + width / 2;
  const bodyWidth = Math.max(2, width * 0.6);
  const bodyX = x + (width - bodyWidth) / 2;

  return (
    <g>
      <line x1={wickX} x2={wickX} y1={y} y2={y + height} stroke={color} strokeWidth={1} />
      <rect x={bodyX} y={bodyTop} width={bodyWidth} height={bodyHeight} fill={color} />
    </g>
  );
}

export default function CandlestickChart({ data }: { data: CandleDatum[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#1c2532" strokeDasharray="3 3" />
        <XAxis dataKey="time" stroke="#6e7681" fontSize={11} minTickGap={50} />
        <YAxis stroke="#6e7681" fontSize={11} domain={["auto", "auto"]} width={70} />
        <Tooltip
          contentStyle={{ background: "#0f1520", border: "1px solid #1c2532", fontSize: 12 }}
          labelStyle={{ color: "#c9d1d9" }}
          formatter={(value: any, name: string) =>
            typeof value === "number" ? [value.toFixed(5), name] : [value, name]
          }
        />
        <Bar dataKey={(d: CandleDatum) => [d.low, d.high]} fill="transparent" shape={<CandleShape />} isAnimationActive={false} name="OHLC" />
        <Line type="monotone" dataKey="ema_20" stroke="#3b82f6" strokeWidth={1} dot={false} name="EMA 20" />
        <Line type="monotone" dataKey="ema_50" stroke="#f59e0b" strokeWidth={1} dot={false} name="EMA 50" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
