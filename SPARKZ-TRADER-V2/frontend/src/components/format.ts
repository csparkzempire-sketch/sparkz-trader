// Number formatting shared by every page.

export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function money(v: unknown, digits = 2): string {
  if (!isNum(v)) return "—";
  const s = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${v < 0 ? "−" : ""}$${s}`;
}

export function signedMoney(v: unknown): string {
  if (!isNum(v)) return "—";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function pct(v: unknown, digits = 2, signed = false): string {
  if (!isNum(v)) return "—";
  const sign = signed && v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}${Math.abs(v).toFixed(digits)}%`;
}

export function num(v: unknown, digits = 2): string {
  if (v === "inf") return "∞";
  if (!isNum(v)) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function price(v: unknown): string {
  if (!isNum(v)) return "—";
  const d = Math.abs(v) >= 1000 ? 2 : Math.abs(v) >= 10 ? 3 : 5;
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

export const tone = (v: unknown): "up" | "down" | "neutral" => (!isNum(v) || v === 0 ? "neutral" : v > 0 ? "up" : "down");

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const label = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
