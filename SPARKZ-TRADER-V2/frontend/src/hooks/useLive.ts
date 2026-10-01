import { useEffect, useRef, useState } from "react";
import { get } from "../services/api";
import { Dict } from "../types/api";

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

