import { useEffect, useState } from "react";
import { HashRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import OverviewPage from "./pages/OverviewPage";
import MarketPage from "./pages/MarketPage";
import PredictionPage from "./pages/PredictionPage";
import BacktestPage from "./pages/BacktestPage";
import TradesPage from "./pages/TradesPage";
import PaperTradingPage from "./pages/PaperTradingPage";
import RiskPage from "./pages/RiskPage";
import ResearchPage from "./pages/ResearchPage";
import ModelLabPage from "./pages/ModelLabPage";
import { AppStateProvider } from "./hooks/useAppState";

const NAV_ITEMS = [
  { to: "/", label: "Overview", exact: true },
  { to: "/market", label: "Market" },
  { to: "/prediction", label: "AI Prediction" },
  { to: "/backtest", label: "Backtesting" },
  { to: "/trades", label: "Trades" },
  { to: "/paper", label: "Paper Trading" },
  { to: "/research", label: "Research" },
  { to: "/risk", label: "Risk" },
  { to: "/model-lab", label: "Model Lab" },
];

function Brand() {
  return (
    <div>
      <div className="text-base font-bold tracking-tight text-base-text">SPARKZ TRADER</div>
      <div className="text-[10px] uppercase tracking-widest text-base-muted mt-0.5">Quant Research Terminal</div>
    </div>
  );
}

// Desktop (md and up): a fixed sidebar. Phones: the same sidebar slides in
// over the page from a menu button in the top bar, and closes on navigation.
function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <>
      <div
        className={`fixed inset-0 z-30 bg-black/60 md:hidden ${open ? "" : "hidden"}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        id="app-nav"
        className={`fixed inset-y-0 left-0 z-40 w-64 max-w-[85vw] transform transition-transform duration-200
          md:static md:z-auto md:w-56 md:max-w-none md:translate-x-0 md:transition-none
          shrink-0 border-r border-base-border bg-base-panel flex flex-col
          ${open ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="px-4 py-5 border-b border-base-border flex items-start justify-between">
          <Brand />
          <button
            onClick={onClose}
            className="md:hidden -mr-1 p-1 text-base-muted hover:text-base-text"
            aria-label="Close menu"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <nav className="flex-1 py-3 overflow-y-auto">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.exact}
              className={({ isActive }) =>
                `block px-4 py-2.5 md:py-2 text-sm border-l-2 transition-colors ${
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
    </>
  );
}

function TopBar({ onMenu }: { onMenu: () => void }) {
  const { pathname } = useLocation();
  const current = NAV_ITEMS.find((i) => (i.exact ? pathname === i.to : pathname.startsWith(i.to)));
  return (
    <header className="md:hidden sticky top-0 z-20 flex items-center gap-3 px-4 h-14 border-b border-base-border bg-base-panel">
      <button
        onClick={onMenu}
        className="-ml-1 p-1.5 text-base-text"
        aria-label="Open menu"
        aria-controls="app-nav"
      >
        <svg width="22" height="22" viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M3 6h16M3 11h16M3 16h16" strokeLinecap="round" />
        </svg>
      </button>
      <span className="text-sm font-bold tracking-tight">SPARKZ TRADER</span>
      {current && <span className="text-sm text-base-muted truncate">· {current.label}</span>}
    </header>
  );
}

function Shell() {
  const [menuOpen, setMenuOpen] = useState(false);
  const { pathname } = useLocation();

  useEffect(() => setMenuOpen(false), [pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar open={menuOpen} onClose={() => setMenuOpen(false)} />
      <main className="flex-1 min-w-0 overflow-y-auto">
        <TopBar onMenu={() => setMenuOpen(true)} />
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/market" element={<MarketPage />} />
          <Route path="/prediction" element={<PredictionPage />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/trades" element={<TradesPage />} />
          <Route path="/paper" element={<PaperTradingPage />} />
          <Route path="/research" element={<ResearchPage />} />
          <Route path="/risk" element={<RiskPage />} />
          <Route path="/model-lab" element={<ModelLabPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AppStateProvider>
      <HashRouter>
        <Shell />
      </HashRouter>
    </AppStateProvider>
  );
}
