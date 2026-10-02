# SPARKZ TRADER V2

**Broker-connected market data + adaptive grid & basket simulator.**

Real (or synthetic) prices go in. A grid/basket strategy decides. Every order is **simulated** against a
**PAPER ACCOUNT**. There is no route to a real order anywhere in this code:

- `live_trading_enabled` is rejected by the configuration;
- the broker provider has only read endpoints;
- the only working execution adapter is the simulator.

> Results in this repository describe specific periods under stated assumptions. They are not forecasts,
> and nothing here claims the strategy makes money. Read `reports/TECHNICAL_REPORT.md` before reading any number.

## Architecture

```
MarketDataProvider ──ticks/candles──▶ MarketEngine ──MarketState──▶ AdaptiveGridBasketStrategy
 (mock · historical · Yahoo ·                (indicators, regime)           │ analysis → entry → grid → basket
  OANDA read-only)                                                         ▼
                                                                     OrderIntent
                                                                           │
                                         RiskManager (pre-trade gate: limits, cooldown, stale data, stop)
                                                                           │
                                         ExecutionAdapter = SimulationExecutor (spread, slippage, delay)
                                                                           │ Fill
                                     PaperAccount ◀── Strategy.on_fill ◀───┘      EventLog ─▶ SQLite / WebSocket
```

The same `Robot` (in `engine/robot.py`) runs both the backtest (`backtest/engine.py`, closed candles plus an
intrabar price path) and paper trading (`paper/runner.py`, a live provider polled every few seconds).

## Layout

```
backend/app/
  config.py                  settings, safety validation, env-variable mapping, presets
  market/providers/          base.py (interface), mock, historical, yahoo, broker (OANDA, read-only)
  market/                    instruments, indicators, regime, market_engine, history store, sessions
  strategy/                  analysis, entry_engine, grid_engine, position_sizing, basket_manager, strategy
  risk/                      risk_manager (pre-trade gate), drawdown, exposure, margin
  execution/                 order_intent, fill_engine, simulator (SimulationExecutor; LiveExecutionAdapter refuses)
  paper/                     account (PAPER ACCOUNT), positions, trades, runner (live loop + stale-data guard)
  engine/                    robot (orchestrator, status machine, emergency stop), event_log, health
  backtest/                  engine, metrics, stress_test, walk_forward (+ parameter lab)
  database/                  SQLAlchemy models + repository (runs, events, baskets, fills, equity)
  api/ + main.py             FastAPI routes and the /ws/live WebSocket
  research/studies.py        the studies behind the technical report
  cli.py                     command line
backend/tests/               pytest suite
config/presets/              baseline, fixed_grid, atr_grid, signal_grid, video_style, multiplier_high_risk
frontend/                    React + Vite + TypeScript + Tailwind + Recharts dashboard
reports/                     TECHNICAL_REPORT.md and study outputs
```

## Run it

```bash
cd SPARKZ-TRADER-V2
cp .env.example .env              # optional; real env variables take precedence
cd backend && pip install -r requirements.txt

python -m pytest                   # test suite
python -m app.cli presets          # list presets
python -m app.cli download --timeframe 15m          # XAUUSD from Yahoo (GC=F futures as a proxy)
python -m app.cli backtest --preset atr_grid
python -m app.cli backtest --preset video_style --set execution.intrabar_order=RANDOM
python -m app.cli stress --preset atr_grid
python -m app.cli walk-forward --preset atr_grid --space grid.atr_multiplier=0.5,1.0
python -m app.cli lab --preset atr_grid --space grid.atr_multiplier=0.5,1.0 --space target.fixed_usd=5,10,20

uvicorn app.main:app --port 8000   # API + paper loop (MOCK data unless MARKET_DATA_PROVIDER says otherwise)
cd ../frontend && npm install && npm run dev        # dashboard on http://localhost:5173
#   or: npm run build — the API then serves frontend/dist on http://localhost:8000
```

### Market data

| Provider | `MARKET_DATA_PROVIDER` | What it is |
|---|---|---|
| Mock | `MOCK` | Synthetic prices (scenarios: normal, trends, sideways, high_vol, spike, reversals, news_spikes, adverse_trend). Labelled "not market data". |
| Historical | `HISTORICAL` | Stored candles replayed (`data/candles/`). |
| Yahoo | `YAHOO` | Delayed public data; gold is the GC=F future. No real bid/ask: the instrument's typical spread is used. |
| Broker | `BROKER` | OANDA v20 practice or live account. Real bid/ask and candles. Read-only. Needs `OANDA_API_TOKEN` and `OANDA_ACCOUNT_ID` in the environment. |

Credentials are read only from environment variables. They are never placed in the frontend, the repository,
logs or reports, and `repr()` hides the token.

### Long fine-grained history from OANDA (read-only)

With `OANDA_API_TOKEN` and `OANDA_ACCOUNT_ID` set, and `api-fxpractice.oanda.com` allowed:

```bash
python -m app.cli download --source oanda --timeframe 15m --start 2024-01-01
python -m app.cli download --source oanda --timeframe 1m  --start 2024-01-01   # ~150 requests, a few minutes
python -m app.cli backtest --source oanda --preset video_style --path 1m      # 1m candles as the intrabar path
python -m app.research.fine_path_study --source oanda --path 1m               # every preset x every intrabar order
```

OANDA candles are spot XAU_USD and are kept in their own store (`data/candles/oanda/`). They are never mixed
with Yahoo's GC=F futures, which trade at a different price level. The download also reports the broker's
real spread (median and 90th/99th percentile), to check the backtest's 0.30 spread assumption.

### Daily candle archive

Yahoo keeps 1-minute gold candles for only 7 days and 5-minute candles for 60. Run
`python -m app.cli archive` once a day and every completed UTC day is saved as its own write-once
file, `data/archive/XAUUSD/<1m|5m>/<YYYY-MM-DD>.csv.gz`, at about 21 kB a day. The history
then grows past Yahoo's limit. `load_history` merges the archive in automatically, so backtests can use it
(e.g. `path_candles=load_history("XAUUSD", "1m")` as the intrabar price path).

A daily routine commits the archive to the `market-data` branch, not `main`.
`python -m app.cli archive-status` shows the coverage.

### Safety controls

- **STOP ROBOT** (dashboard header, always visible) or `POST /api/system/emergency-stop`:
  - stops new entries, grid adds and all strategy processing;
  - cancels pending simulated orders;
  - keeps positions as they are and keeps marking them.
  Only a person can resume.
- **Account drawdown limit:** closes the basket and halts until resumed.
- **Stale data:** no new positions until fresh prices arrive.
- **Risk gate:** every OPEN and ADD order must pass these limits; closes are never blocked.
  - max positions (hard ceiling 20)
  - basket loss, basket drawdown and daily loss
  - exposure and margin
  - cooldown
- **MULTIPLIER sizing:** refused unless `ALLOW_MULTIPLIER_SIZING=true`; flagged HIGH RISK everywhere.

### VIDEO_STYLE_MODE

`VIDEO_STYLE_MODE=true` loads `config/presets/video_style.yaml`: XAUUSD 15m, a fixed 2.0 grid, LINEAR lots,
a $10 basket target and max 5 positions. It approximates the behaviour described in the reference video.
It is **not** that video's proprietary algorithm, whose rules are not public.
