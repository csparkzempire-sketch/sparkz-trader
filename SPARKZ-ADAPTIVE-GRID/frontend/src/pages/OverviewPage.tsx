import { Link } from "react-router-dom";
import { api } from "../services/api";
import { Direction, Empty, Page, PageHeader, Panel, Regime, StatCard, StatGrid, Table } from "../components/ui";
import { useAppState, useLoad } from "../components/state";
import { money, num, pct, signedMoney, tone, when } from "../components/format";
import { EquityChart } from "../charts/Charts";

export default function OverviewPage() {
  const { account, symbol, timeframe, setBacktestId } = useAppState();
  const accounts = useLoad(() => api.paperAccounts(), []);
  const name = account ?? accounts.data?.[0]?.name ?? null;
  const st = useLoad(() => (name ? api.paperStatus(name) : Promise.resolve(null)), [name]);
  const analysis = useLoad(() => api.analysis(symbol, timeframe), [symbol, timeframe]);
  const runs = useLoad(() => api.listBacktests(), []);
  const s = st.data;
  const today = (() => {
    if (!s?.equity_curve.length) return null;
    const d = new Date().toISOString().slice(0, 10);
    const pts = s.equity_curve.filter((p) => p.t.slice(0, 10) === d);
    return pts.length ? pts[pts.length - 1].equity - pts[0].equity : 0;
  })();

  return (
    <>
      <PageHeader title="Overview" subtitle="Research and paper simulation of an adaptive grid and basket strategy. Every figure here is simulated." />
      <Page>
        {s ? (
          <>
            <StatGrid cols={6}>
              <StatCard label={`Equity · ${s.name}`} value={money(s.equity)} hint={`${s.symbol} ${s.timeframe}`} />
              <StatCard label="Total P&L" value={signedMoney(s.pnl)} tone={tone(s.pnl)} />
              <StatCard label="Today's P&L" value={signedMoney(today)} tone={tone(today)} />
              <StatCard label="Drawdown" value={pct(s.drawdown_pct)} tone={s.drawdown_pct > 0 ? "down" : "neutral"} />
              <StatCard label="Open positions" value={String(s.open_basket?.positions ?? 0)} hint={s.open_basket ? `${s.open_basket.direction} basket ${s.open_basket.basket_id}` : "no basket"} />
              <StatCard label="Risk status" value={s.halted ? "HALTED" : "OK"} tone={s.halted ? "down" : "up"} />
            </StatGrid>
            {s.equity_curve.length > 1 && <Panel title="Virtual equity"><EquityChart curve={s.equity_curve} height={220} /></Panel>}
          </>
        ) : (
          <Panel><Empty>No paper account yet. <Link className="text-accent-brass font-semibold" to="/paper">Create one</Link>, or start with a <Link className="text-accent-brass font-semibold" to="/backtest">backtest</Link>.</Empty></Panel>
        )}
        <div className="grid lg:grid-cols-2 gap-5">
          <Panel title={`Market now · ${symbol} ${timeframe}`} right={<Link to="/market" className="text-accent-brass text-sm font-semibold">Market →</Link>}>
            {analysis.data ? (
              <div className="grid gap-2 text-sm">
                <div className="flex flex-wrap gap-4 items-center"><Regime r={analysis.data.regime} /><Regime r={analysis.data.vol_regime} /><Direction d={analysis.data.signal} /></div>
                <div className="text-base-muted">{analysis.data.reasons.join("; ")} · bar {when(analysis.data.timestamp)} · ATR {num(analysis.data.features.atr)}</div>
              </div>
            ) : <Empty>{analysis.error ?? "Loading…"}</Empty>}
          </Panel>
          <Panel title="Recent backtests" right={<Link to="/backtest" className="text-accent-brass text-sm font-semibold">Backtest →</Link>}>
            <Table head={["#", "Name", "Baskets", "Net P&L", "Max DD"]} align={["l", "l", "r", "r", "r"]}
              rows={(runs.data ?? []).slice(0, 6).map((r) => [
                <Link to="/performance" onClick={() => setBacktestId(r.id)} className="text-accent-brass">{r.id}</Link>, r.name, r.baskets ?? "—",
                <span className={tone(r.net_pnl) === "down" ? "text-accent-down" : "text-accent-up"}>{money(r.net_pnl)}</span>, pct(r.max_drawdown_pct)])}
              empty="No runs yet." />
          </Panel>
        </div>
      </Page>
    </>
  );
}
