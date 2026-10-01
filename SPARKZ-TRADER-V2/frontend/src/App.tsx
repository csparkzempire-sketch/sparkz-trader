import { useEffect, useState } from "react";
import { ErrorBox } from "./components/ui";
import EventsPage from "./pages/EventsPage";
import LivePage, { StatusWord } from "./pages/LivePage";
import ResearchPage, { Kind } from "./pages/ResearchPage";
import SystemPage from "./pages/SystemPage";
import { useLive } from "./hooks/useLive";
import { post } from "./services/api";

const NAV: [string, string][] = [["live", "Live"], ["events", "Event log"], ["run", "Backtest"], ["stress", "Stress tests"],
  ["walk-forward", "Walk-forward"], ["lab", "Parameter lab"], ["system", "System"]];

function StopButton({ stopped, onDone }: { stopped: boolean; onDone: (e: string | null) => void }) {
  const [busy, setBusy] = useState(false);
  const act = async () => {
    if (!stopped && !confirm("STOP ROBOT: stop new entries, grid expansion and strategy processing, and cancel pending orders? Open positions stay as they are.")) return;
    setBusy(true);
    try { await post(stopped ? "/api/system/resume" : "/api/system/emergency-stop"); onDone(null); }
    catch (e) { onDone((e as Error).message); }
    finally { setBusy(false); }
  };
  return stopped ? (
    <button onClick={act} disabled={busy} className="px-4 py-2 border border-base-border text-sm font-semibold hover:bg-base-tint">RESUME ROBOT</button>
  ) : (
    <button onClick={act} disabled={busy} className="px-5 py-2.5 bg-accent-down text-white text-sm font-bold tracking-[0.12em] shadow-lg hover:opacity-90">STOP ROBOT</button>
  );
}

export default function App() {
  const [page, setPage] = useState(() => location.hash.slice(1) || "live");
  const { data, conn, lastUpdate } = useLive();
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { const f = () => setPage(location.hash.slice(1) || "live"); addEventListener("hashchange", f); return () => removeEventListener("hashchange", f); }, []);
  const stopped = !!(data?.emergency_stop || data?.halted);
  return (
    <div className="min-h-full md:grid md:grid-cols-[13rem_minmax(0,1fr)]">
      <aside className="bg-base-side/90 border-r border-base-border md:min-h-screen">
        <div className="px-4 py-5">
          <div className="font-display text-xl leading-tight">Sparkz Trader <span className="text-accent-brand">V2</span></div>
          <div className="text-[11px] text-base-muted mt-1">Market data + grid &amp; basket simulator</div>
        </div>
        <nav className="flex md:flex-col overflow-x-auto px-2 pb-3 gap-0.5">
          {NAV.map(([id, name]) => (
            <a key={id} href={`#${id}`} className={`px-3 py-2 text-sm whitespace-nowrap ${page === id ? "bg-base-tint text-base-text border-l-2 border-accent-brand" : "text-base-muted hover:text-base-text"}`}>{name}</a>
          ))}
        </nav>
      </aside>
      <main className="min-w-0">
        <div className="sticky top-0 z-10 bg-base-bg/90 backdrop-blur border-b border-base-border px-4 md:px-6 py-2.5 flex flex-wrap items-center gap-x-5 gap-y-2">
          <span className="text-[11px] font-bold tracking-[0.14em] px-2 py-1 border border-accent-warn text-accent-warn">PAPER ACCOUNT · SIMULATION · LIVE TRADING DISABLED</span>
          {data && <span className="scale-75 origin-left -my-2"><StatusWord s={data.status} /></span>}
          <span className="text-xs text-base-muted">
            {conn === "live" ? "● live" : conn === "polling" ? "● polling" : conn === "offline" ? "○ offline" : "connecting"}
            {lastUpdate ? ` · ${new Date(lastUpdate).toLocaleTimeString()}` : ""}
            {data?.health ? ` · health ${data.health.overall}` : ""}
          </span>
          <span className="ml-auto"><StopButton stopped={stopped} onDone={setErr} /></span>
        </div>
        {err && <div className="px-4 md:px-6 pt-3"><ErrorBox error={err} /></div>}
        {page === "live" && <LivePage d={data} />}
        {page === "events" && <EventsPage live={data} />}
        {(["run", "stress", "walk-forward", "lab"] as Kind[]).includes(page as Kind) && <ResearchPage key={page} kind={page as Kind} />}
        {page === "system" && <SystemPage live={data} />}
      </main>
    </div>
  );
}
