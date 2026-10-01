// API client and the live connection (WebSocket with a polling fallback).
import { useEffect, useRef, useState } from "react";

export type Dict = Record<string, any>;

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

export type Connection = "connecting" | "live" | "polling" | "offline";

/** Dashboard state pushed by /ws/live; falls back to polling /api/dashboard every 3 s. */
export function useLive(): { data: Dict | null; conn: Connection; lastUpdate: number | null; refresh: () => void } {
  const [data, setData] = useState<Dict | null>(null);
  const [conn, setConn] = useState<Connection>("connecting");
  const [lastUpdate, setLast] = useState<number | null>(null);
  const poll = useRef<number | null>(null);

  const refresh = () =>
    get("/api/dashboard")
      .then((d) => { setData(d); setLast(Date.now()); })
      .catch(() => setConn("offline"));

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    const startPolling = () => {
      if (poll.current) return;
      setConn("polling");
      refresh();
      poll.current = window.setInterval(refresh, 3000);
    };
    try {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/live`);
      ws.onopen = () => {
        setConn("live");
        if (poll.current) { clearInterval(poll.current); poll.current = null; }
      };
      ws.onmessage = (e) => { setData(JSON.parse(e.data)); setLast(Date.now()); };
      ws.onerror = () => startPolling();
      ws.onclose = () => { if (!closed) startPolling(); };
    } catch {
      startPolling();
    }
    return () => {
      closed = true;
      ws?.close();
      if (poll.current) clearInterval(poll.current);
    };
  }, []);

  return { data, conn, lastUpdate, refresh };
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
