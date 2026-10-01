// Shapes returned by the API. Payloads are wide and evolve with the backend, so most are kept open.
export type Dict = Record<string, any>;

export interface Account {
  label: "PAPER ACCOUNT"; simulated: true; initial_balance: number; balance: number; equity: number;
  floating_pnl: number; realized_pnl: number; used_margin: number; free_margin: number; margin_level_pct: number | null;
  daily_pnl: number; drawdown_pct: number; max_drawdown_pct: number; open_positions: number; return_pct: number;
}

export type RobotStatus = "ANALYZING" | "WAITING" | "ENTRY" | "ADDING_POSITION" | "MANAGING_BASKET" | "TARGET_REACHED"
  | "CLOSING" | "COOLDOWN" | "STOPPED";

export interface Dashboard extends Dict {
  status: RobotStatus; account: Account; live_trading_enabled: false; emergency_stop: string | null; halted: string | null;
  data_ok: boolean; data_reason: string; market: Dict | null; basket: Dict | null; events: Dict[];
}
