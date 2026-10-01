import { useEffect, useState } from "react";
import { num, when } from "../components/format";
import { Button, ErrorBox, HighRisk, inputCls, KeyValues, Page, PageHeader, Panel, Table } from "../components/ui";
import { Dict, get, post } from "../services/api";

const yes = (v: unknown) => (v === true ? "yes" : v === false ? <span className="text-accent-down">no</span> : "—");

export default function SystemPage({ live }: { live: Dict | null }) {
  const h = live?.health;
  const [cfg, setCfg] = useState<Dict | null>(null);
  const [preset, setPreset] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { get("/api/system/config").then(setCfg).catch((e) => setErr(String(e))); }, []);
  const restart = async () => {
    setErr(null); setMsg(null);
    try { const r = await post("/api/system/restart", { preset: preset || null }); setMsg(`New paper session with preset ${r.preset}.`); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <>
      <PageHeader title="System" subtitle="Health of the data feed, the robot, the simulated executor and the database." />
      <Page>
        {h && (
          <div className="grid xl:grid-cols-3 gap-5">
            <Panel title={`Health: ${h.overall}`}>
              <KeyValues items={[["Data provider", h.data.provider], ["Live prices", yes(h.data.live_prices)], ["Delayed", h.data.delayed ? "yes" : "no"],
                ["Data fresh", yes(h.data.fresh)], ["Reason", h.data.reason || "—"], ["Last tick", when(h.data.last_tick_at)],
                ["Data age", h.data.data_age_seconds == null ? "—" : `${num(h.data.data_age_seconds, 0)} s (limit ${h.data.stale_after_seconds} s)`],
                ["Market open", yes(h.data.market_open)]]} />
            </Panel>
            <Panel title="Robot and executor">
              <KeyValues items={[["Strategy", h.strategy.name], ["Status", h.strategy.status], ["Running", yes(h.strategy.running)],
                ["Emergency stop", h.strategy.emergency_stop ?? "no"], ["Halted", h.strategy.halted ?? "no"], ["Bars processed", h.strategy.bars_processed],
                ["Executor", h.executor.kind], ["Live trading", h.executor.live_trading_enabled ? "ENABLED" : "disabled"], ["Pending orders", h.executor.pending_orders]]} />
            </Panel>
            <Panel title="Database and process">
              <KeyValues items={[["Database", yes(h.database.connected)], ["Write errors", h.database.write_errors ?? 0], ["CPU", `${num(h.process.cpu_percent, 1)}%`],
                ["Memory", `${num(h.process.memory_mb, 0)} MB`], ["System memory", `${num(h.process.system_memory_percent, 0)}%`],
                ["Uptime", `${num((h.process.uptime_seconds ?? 0) / 60, 0)} min`], ["Errors", h.errors.count], ["Last error", h.errors.last ?? "—"]]} />
            </Panel>
          </div>
        )}
        <Panel title="Presets">
          <Table head={["Preset", "Grid", "Sizing", "Target", "Max pos", ""]}
            rows={(cfg?.presets ?? []).map((p: Dict) => [p.name, p.grid, p.sizing, p.target, p.max_positions,
              <span>{p.high_risk.length > 0 && <HighRisk />}{p.video_style_mode && <span className="text-xs text-base-muted"> approximation of the video, not its algorithm</span>}</span>])} />
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <select className={`${inputCls} w-64`} value={preset} onChange={(e) => setPreset(e.target.value)}>
              <option value="">default</option>{(cfg?.presets ?? []).map((p: Dict) => <option key={p.name}>{p.name}</option>)}
            </select>
            <Button kind="ghost" onClick={restart}>Start a new paper session</Button>
            <span className="text-xs text-base-muted">Starts a fresh PAPER ACCOUNT. Refused while a basket is open.</span>
          </div>
          {msg && <p className="text-sm text-accent-up mt-2">{msg}</p>}
        </Panel>
        <ErrorBox error={err} />
        {cfg && <Panel title="Current settings"><pre className="text-xs overflow-x-auto">{JSON.stringify(cfg.current, null, 2)}</pre></Panel>}
      </Page>
    </>
  );
}
