import { HashRouter, NavLink, Route, Routes } from "react-router-dom";
import OverviewPage from "./pages/OverviewPage";
import MarketPage from "./pages/MarketPage";
import PredictionPage from "./pages/PredictionPage";
import BacktestPage from "./pages/BacktestPage";
import TradesPage from "./pages/TradesPage";
import RiskPage from "./pages/RiskPage";
import ModelLabPage from "./pages/ModelLabPage";
import { AppStateProvider } from "./hooks/useAppState";

const NAV_ITEMS = [
  { to: "/", label: "Overview", exact: true },
  { to: "/market", label: "Market" },
  { to: "/prediction", label: "AI Prediction" },
  { to: "/backtest", label: "Backtesting" },
  { to: "/trades", label: "Trades" },
  { to: "/risk", label: "Risk" },
  { to: "/model-lab", label: "Model Lab" },
];

function Sidebar() {
  return (
    <aside className="w-56 shrink-0 border-r border-base-border bg-base-panel flex flex-col">
      <div className="px-4 py-5 border-b border-base-border">
        <div className="text-base font-bold tracking-tight text-base-text">SPARKZ TRADER</div>
        <div className="text-[10px] uppercase tracking-widest text-base-muted mt-0.5">Quant Research Terminal</div>
      </div>
      <nav className="flex-1 py-3">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.exact}
            className={({ isActive }) =>
              `block px-4 py-2 text-sm border-l-2 transition-colors ${
                isActive
                  ? "border-accent-brand text-base-text bg-white/5"
                  : "border-transparent text-base-muted hover:text-base-text hover:bg-white/5"
              }`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="px-4 py-3 border-t border-base-border text-[10px] text-base-muted leading-relaxed">
        Paper trading only. No real-money orders are placed. Historical performance does not guarantee future
        results.
      </div>
    </aside>
  );
}

export default function App() {
  return (
    <AppStateProvider>
      <HashRouter>
        <div className="flex h-screen overflow-hidden">
          <Sidebar />
          <main className="flex-1 overflow-y-auto">
            <Routes>
              <Route path="/" element={<OverviewPage />} />
              <Route path="/market" element={<MarketPage />} />
              <Route path="/prediction" element={<PredictionPage />} />
              <Route path="/backtest" element={<BacktestPage />} />
              <Route path="/trades" element={<TradesPage />} />
              <Route path="/risk" element={<RiskPage />} />
              <Route path="/model-lab" element={<ModelLabPage />} />
            </Routes>
          </main>
        </div>
      </HashRouter>
    </AppStateProvider>
  );
}
