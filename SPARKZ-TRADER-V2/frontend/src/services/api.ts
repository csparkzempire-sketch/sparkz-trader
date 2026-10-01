// API client and the live connection (WebSocket with a polling fallback).
import { Dict } from "../types/api";

export type { Dict };

export async function get<T = any>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

export async function post<T = any>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
  const text = await r.text();
  if (!r.ok) {
    let msg = text;
    try { msg = JSON.parse(text).detail ?? text; } catch { /* plain text */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return text ? JSON.parse(text) : ({} as T);
}

/** Starts a research job and polls it until it finishes. */
export async function runJob(kind: string, body: Dict, onTick?: (s: string) => void): Promise<Dict> {
  const { job } = await post<{ job: string }>(`/api/backtest/${kind}`, body);
  const t0 = Date.now();
  for (;;) {
    await new Promise((r) => setTimeout(r, 1000));
    const j = await get(`/api/backtest/jobs/${job}`);
    onTick?.(`${Math.round((Date.now() - t0) / 1000)} s`);
    if (j.status === "done") return j.result;
    if (j.status === "error") throw new Error(j.error);
  }
}
