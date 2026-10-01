import { useEffect, useMemo, useState } from "react";
import { inputCls, Page, PageHeader, Panel } from "../components/ui";
import { Dict, get } from "../services/api";
import { EventList } from "./LivePage";

export default function EventsPage({ live }: { live: Dict | null }) {
  const [events, setEvents] = useState<Dict[]>([]);
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const seq = live?.events?.length ? live.events[live.events.length - 1].seq : 0;
  useEffect(() => { get("/api/system/events?limit=2000").then(setEvents).catch(() => {}); }, [seq]);
  const types = useMemo(() => Array.from(new Set(events.map((e) => e.type))).sort(), [events]);
  const shown = events.filter((e) => (!type || e.type === type) && (!q || e.message.toLowerCase().includes(q.toLowerCase())));
  return (
    <>
      <PageHeader title="Event log" subtitle="Every analysis, signal, order intent, fill, risk decision, close, reset, stop and error, in order." />
      <Page>
        <Panel title={`${shown.length} of ${events.length} events`} right={
          <span className="flex gap-2">
            <select className={`${inputCls} w-48`} value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">all types</option>{types.map((t) => <option key={t}>{t}</option>)}
            </select>
            <input className={`${inputCls} w-56`} placeholder="search" value={q} onChange={(e) => setQ(e.target.value)} />
          </span>}>
          <EventList events={shown} max={1000} />
        </Panel>
      </Page>
    </>
  );
}
