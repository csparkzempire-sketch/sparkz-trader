import { useEffect, useState } from "react";
import { HashRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { AppStateProvider } from "./components/state";
import OverviewPage from "./pages/OverviewPage";
import MarketPage from "./pages/MarketPage";
import BasketPage from "./pages/BasketPage";
import BacktestPage from "./pages/BacktestPage";
import PerformancePage from "./pages/PerformancePage";
import ComparisonPage from "./pages/ComparisonPage";
import RobustnessPage from "./pages/RobustnessPage";
import RiskPage from "./pages/RiskPage";
import PaperPage from "./pages/PaperPage";

const NAV = [
  { to: "/", label: "Overview", exact: true },
  { to: "/market", label: "Market" },
  { to: "/basket", label: "Basket" },
  { to: "/backtest", label: "Backtest" },
  { to: "/performance", label: "Performance" },
  { to: "/compare", label: "Comparison Lab" },
  { to: "/robustness", label: "Stress & Robustness" },
  { to: "/risk", label: "Risk" },
  { to: "/paper", label: "Paper Trading" },
];

function Brand() {
  return (
    <div>
      <div className="font-display text-[1.3rem] leading-tight text-base-text">SPARKZ</div>
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-accent-brass mt-1">Adaptive grid · basket research</div>
      <div className="mt-4 h-[5px] border-t-2 border-b border-base-text" aria-hidden="true" />
    </div>
  );
}

function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <>
      <div className={`fixed inset-0 z-30 bg-black/60 md:hidden ${open ? "" : "hidden"}`} onClick={onClose} aria-hidden="true" />
      <aside
        id="app-nav"
        className={`fixed inset-y-0 left-0 z-40 w-64 max-w-[85vw] transform transition-transform duration-200
          md:static md:z-auto md:w-56 md:max-w-none md:translate-x-0 md:transition-none
          shrink-0 border-r border-base-border bg-base-side/85 backdrop-blur flex flex-col
          ${open ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="px-4 py-5 border-b border-base-border flex items-start justify-between">
          <Brand />
          <button onClick={onClose} className="md:hidden -mr-1 p-1 text-base-muted hover:text-base-text" aria-label="Close menu">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" /></svg>
          </button>
        </div>
        <nav className="flex-1 py-3 overflow-y-auto">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.exact}
              className={({ isActive }) => `block px-4 py-2.5 md:py-2 text-sm border-l-2 transition-colors ${isActive
                ? "border-accent-brass text-base-text bg-base-tint/80 font-semibold"
                : "border-transparent text-base-muted hover:text-base-text hover:bg-base-panel/70"}`}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-3 border-t border-base-border text-[10px] text-base-muted leading-relaxed">
          Research and paper simulation only. No broker is connected and no real orders exist. Backtests describe the
          past; they are not a forecast.
        </div>
      </aside>
    </>
  );
}

function TopBar({ onMenu }: { onMenu: () => void }) {
  const { pathname } = useLocation();
  const cur = NAV.find((n) => (n.exact ? pathname === n.to : pathname.startsWith(n.to)));
  return (
    <header className="md:hidden sticky top-0 z-20 flex items-center gap-3 px-4 h-14 border-b border-base-border bg-base-side/90 backdrop-blur">
      <button onClick={onMenu} className="-ml-1 p-1.5 text-base-text" aria-label="Open menu" aria-controls="app-nav">
        <svg width="22" height="22" viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M3 6h16M3 11h16M3 16h16" strokeLinecap="round" /></svg>
      </button>
      <span className="font-display text-lg leading-none">SPARKZ</span>
      {cur && <span className="text-sm text-base-muted truncate">· {cur.label}</span>}
    </header>
  );
}

function Shell() {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => setOpen(false), [pathname]);
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar open={open} onClose={() => setOpen(false)} />
      <main className="flex-1 min-w-0 overflow-y-auto">
        <TopBar onMenu={() => setOpen(true)} />
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/market" element={<MarketPage />} />
          <Route path="/basket" element={<BasketPage />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/performance" element={<PerformancePage />} />
          <Route path="/compare" element={<ComparisonPage />} />
          <Route path="/robustness" element={<RobustnessPage />} />
          <Route path="/risk" element={<RiskPage />} />
          <Route path="/paper" element={<PaperPage />} />
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
