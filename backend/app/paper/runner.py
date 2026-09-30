"""
Run-once paper trading with state saved to disk.

The live-feed scheduler (app.paper.scheduler) keeps its account in the API
server's memory, which is fine for watching an hourly strategy for an
afternoon but loses everything on restart -- useless for a daily strategy
that needs weeks of history. This runner is meant to be invoked once per
candle (e.g. daily from cron): it loads the account from a JSON file,
processes every candle that has CLOSED since the last run, and saves.

Per run:
  1. Download (or read cached) data and keep only closed candles.
  2. For each closed candle newer than the last one processed, in order,
     check the open position's stop/target against that candle's
     high/low. Candles missed while the runner wasn't running still get
     their stops checked, so a missed day can't hide a stop-out.
  3. Compute the signal on the NEWEST closed candle only and, if flat and
     the signal says so, open a position at its close. Entries are never
     back-filled on missed candles: a trader who wasn't watching couldn't
     have taken them.

Drawdown halt: if the account falls max_drawdown_pct below its peak, the
risk manager blocks new entries. Nothing ever lifts that on its own (the
account can't make back the loss without trading), so the runner marks
the account HALTED -- saved in the state file, logged once, and shown by
the CLI, API and dashboard -- until someone explicitly resumes it with
`paper-trade --account NAME --resume`.

No broker code: this drives app.paper.simulator only.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from app.config import Settings, settings
from app.data.downloader import download_ohlcv
from app.data.repository import DATA_DIR
from app.data.validator import closed_candles, validate_and_clean
from app.features.feature_engineering import build_feature_matrix
from app.paper.simulator import PaperAccountState, PaperPosition, PaperTradeRecord, PaperTradingSimulator
from app.risk.risk_manager import RiskState
from app.strategy.rules import RULE_STRATEGIES, rule_signal

PAPER_DIR = DATA_DIR / "paper"


@dataclass
class PaperRunConfig:
    account_name: str
    symbol: str
    timeframe: str
    strategy: str
    starting_balance: float


@dataclass
class PaperRunState:
    config: PaperRunConfig
    account: PaperAccountState
    last_processed: datetime | None = None
    log: list[str] = field(default_factory=list)
    halted: str | None = None  # why/when new entries stopped; None while trading normally
    # Pass/fail targets fixed from this setup's backtest (app.paper.evaluation).
    # Set once and kept, so the goalposts can't move after results come in.
    targets: dict | None = None
    # Worst stretches of the same backtest (app.paper.norms): reference figures for the
    # normal-losses check. Not pass/fail targets, so they may be added later.
    norms: dict | None = None


def state_path(account_name: str) -> Path:
    return PAPER_DIR / f"{account_name}.json"


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def save_state(state: PaperRunState, path: Path | None = None) -> Path:
    path = path or state_path(state.config.account_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    acct = state.account
    rs = acct.risk_state
    payload = {
        "config": asdict(state.config),
        "last_processed": state.last_processed.isoformat() if state.last_processed else None,
        "account": {
            "balance": acct.balance,
            "is_active": acct.is_active,
            "open_positions": [
                {**asdict(p), "opened_at": p.opened_at.isoformat()} for p in acct.open_positions.values()
            ],
            "trade_history": [
                {**asdict(t), "opened_at": t.opened_at.isoformat(), "closed_at": t.closed_at.isoformat()}
                for t in acct.trade_history
            ],
            "risk_state": {
                **asdict(rs),
                "current_day": rs.current_day.isoformat() if rs.current_day else None,
            },
        },
        "log": state.log[-500:],
        "halted": state.halted,
        "targets": state.targets,
        "norms": state.norms,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)  # atomic: a crash mid-write can't leave a corrupt state file
    return path


def load_state(path: Path) -> PaperRunState:
    raw = json.loads(path.read_text())
    a = raw["account"]
    rs = dict(a["risk_state"])
    rs["current_day"] = date.fromisoformat(rs["current_day"]) if rs.get("current_day") else None
    account = PaperAccountState(balance=a["balance"], is_active=a["is_active"], risk_state=RiskState(**rs))
    for p in a["open_positions"]:
        account.open_positions[p["symbol"]] = PaperPosition(**{**p, "opened_at": _dt(p["opened_at"])})
    account.trade_history = [
        PaperTradeRecord(**{**t, "opened_at": _dt(t["opened_at"]), "closed_at": _dt(t["closed_at"])})
        for t in a["trade_history"]
    ]
    return PaperRunState(
        config=PaperRunConfig(**raw["config"]),
        account=account,
        last_processed=_dt(raw["last_processed"]),
        log=raw.get("log", []),
        halted=raw.get("halted"),
        targets=raw.get("targets"),
        norms=raw.get("norms"),
    )


def _halt(state: PaperRunState, events: list[str], ts: datetime, reason: str) -> None:
    state.halted = f"{ts:%Y-%m-%d %H:%M} UTC: {reason}"
    events.append(
        f"{ts:%Y-%m-%d %H:%M} HALTED {state.config.symbol}: {reason}. No new trades until resumed "
        f"(python -m app.cli paper-trade --account {state.config.account_name} --resume)."
    )


def resume(state: PaperRunState, now: datetime) -> str:
    """Lift a drawdown halt: the current balance becomes the new peak, so the
    max-drawdown limit is measured from here. An explicit, logged decision."""
    rs = state.account.risk_state
    old_peak = rs.peak_equity
    rs.equity = state.account.balance
    rs.peak_equity = state.account.balance
    state.halted = None
    event = (
        f"{now:%Y-%m-%d %H:%M} RESUMED: drawdown limit now measured from balance "
        f"{state.account.balance:.2f} (previous peak {old_peak:.2f})."
    )
    state.log.append(event)
    return event


def list_states() -> list[PaperRunState]:
    """All saved run-once paper accounts, sorted by name."""
    if not PAPER_DIR.exists():
        return []
    return [load_state(p) for p in sorted(PAPER_DIR.glob("*.json"))]


def init_state(config: PaperRunConfig) -> PaperRunState:
    if config.strategy not in RULE_STRATEGIES:
        raise ValueError(f"Paper runner supports rule strategies {RULE_STRATEGIES}, got {config.strategy!r}")
    account = PaperAccountState(balance=config.starting_balance)
    account.is_active = True
    return PaperRunState(config=config, account=account)


@dataclass
class StepResult:
    new_candles: int
    events: list[str]
    latest_candle: datetime | None
    latest_signal: str | None
    latest_close: float | None


def run_step(
    state: PaperRunState,
    candles: pd.DataFrame | None = None,
    cfg: Settings | None = None,
    now: pd.Timestamp | None = None,
) -> StepResult:
    """Process all newly closed candles. `candles` (validated OHLCV) can be
    passed in for tests or offline use; otherwise it is downloaded."""
    cfg = cfg or settings
    c = state.config
    if candles is None:
        raw = download_ohlcv(c.symbol, c.timeframe)
        candles, _ = validate_and_clean(raw, timeframe=c.timeframe, symbol=c.symbol)
    candles = closed_candles(candles, c.timeframe, now=now)

    featured = build_feature_matrix(candles, cfg)
    featured["signal"] = rule_signal(featured, c.strategy, cfg)
    featured = featured.dropna(subset=["atr"]).reset_index(drop=True)
    if featured.empty:
        return StepResult(0, ["Not enough history yet to compute indicators."], None, None, None)

    ts = pd.to_datetime(featured["timestamp"], utc=True)
    if state.last_processed is None:
        # First run: nothing to catch up on, just act on the newest candle.
        new = featured.iloc[[-1]]
    else:
        new = featured[ts > pd.Timestamp(state.last_processed)]

    sim = PaperTradingSimulator(cfg)
    events: list[str] = []
    limit = sim.risk_manager.cfg.max_drawdown_pct
    for row in new.itertuples(index=False):
        bar_ts = row.timestamp.to_pydatetime()
        trade = sim.check_and_close_if_hit(state.account, c.symbol, high=row.high, low=row.low, timestamp=bar_ts)
        if trade is not None:
            events.append(
                f"{bar_ts:%Y-%m-%d %H:%M} CLOSE {trade.direction} {c.symbol} {trade.reason} "
                f"@ {trade.exit_price:.2f}, pnl {trade.pnl:+.2f}, balance {state.account.balance:.2f}"
            )

    latest = featured.iloc[-1]
    latest_ts = latest["timestamp"].to_pydatetime()

    # Flag a breach as soon as a losing close causes it, not only when the
    # next signal happens to be rejected.
    if len(new) and state.halted is None:
        drawdown = sim.risk_manager.status(state.account.risk_state)["current_drawdown_pct"]
        if drawdown >= limit:
            _halt(state, events, latest_ts, f"drawdown {drawdown:.1%} from peak (limit {limit:.0%})")
    signal = str(latest["signal"])
    if len(new) and c.symbol not in state.account.open_positions and signal in ("BUY", "SELL"):
        pos = sim.open_position(
            state.account, c.symbol, signal, raw_price=float(latest["close"]),
            atr_value=float(latest["atr"]), timestamp=latest_ts,
        )
        if pos is not None:
            events.append(
                f"{latest_ts:%Y-%m-%d %H:%M} OPEN {signal} {c.symbol} @ {pos.entry_price:.2f}, size {pos.size:.6f}, "
                f"stop {pos.stop_price:.2f}, target {pos.target_price:.2f}"
            )
        elif (sim.last_rejection or "").startswith("max_drawdown_pct"):
            if state.halted is None:
                _halt(state, events, latest_ts, sim.last_rejection)
            # Already reported as HALTED: don't log every skipped signal again.
        else:
            events.append(f"{latest_ts:%Y-%m-%d %H:%M} {signal} signal skipped: {sim.last_rejection}")

    if len(new):
        state.last_processed = latest_ts
    state.log.extend(events)
    return StepResult(len(new), events, latest_ts, signal, float(latest["close"]))
