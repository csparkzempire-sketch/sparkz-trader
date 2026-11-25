// API client and the live connection (WebSocket with a polling fallback).
import { Dict } from "../types/api";

export type { Dict };

/** Where the SPARKZ server is: "" = the same address as this page (running locally, or one hosted service);
 * set VITE_API_BASE (e.g. https://sparkz.onrender.com) when the dashboard is hosted apart from the server. */
export const API_BASE: string = ((import.meta as any).env?.VITE_API_BASE ?? "").replace(/\/$/, "");

const KEY = "sparkz-dashboard-key";
export function dashboardKey(): string {
  try { return localStorage.getItem(KEY) ?? ""; } catch { return ""; }
}

/** An online server protected by SPARKZ_DASHBOARD_PASSWORD answers 401: ask once, remember it on this device. */
async function call(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = { ...(init.headers as Record<string, string> ?? {}), "X-Dashboard-Key": dashboardKey() };
  const r = await fetch(API_BASE + path, { ...init, headers });
  if (r.status === 401 && retry && !path.startsWith("/api/bridge")) {
    const k = window.prompt("SPARKZ dashboard password:");
    if (k) {
      try { localStorage.setItem(KEY, k); } catch { /* private mode */ }
      return call(path, init, false);
    }
  }
  return r;
}

export async function get<T = any>(path: string): Promise<T> {
  const r = await call(path);
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

export async function post<T = any>(path: string, body?: unknown): Promise<T> {
  const r = await call(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
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
