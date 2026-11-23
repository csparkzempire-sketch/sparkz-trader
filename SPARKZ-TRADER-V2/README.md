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
| MT5 bridge | `BRIDGE` | Quotes and closed 1m candles pushed from your own MetaTrader 5 terminal by `bridge/mt5_bridge.py` (a broker demo works). Real broker bid/ask. Read-only. Needs `SPARKZ_BRIDGE_TOKEN` on both sides. |

Credentials are read only from environment variables. They are never placed in the frontend, the repository,
logs or reports, and `repr()` hides the token.

### Market assistant (multi-timeframe, multi-strategy analysis before every trade)

`app/assistant` reads **1d, 4h, 1h, 15m, 5m and 1m** candles with nine strategies before the robot may open a
trade:

| Strategy | Setup it looks for |
|---|---|
| Trend following | EMA20 > EMA50 > EMA200 (or the reverse), ADX >= 20, price beyond EMA20 |
| Market structure | break of structure (BOS, continuation) or change of character (CHoCH, reversal) |
| Support & resistance | rejection at a level made of several confirmed swing points |
| Supply & demand | price back in a fresh zone: the base candle before an impulsive candle |
| Trendline break | close through the line through the last two lower highs / higher lows |
| S/R flip | a broken level retested from the other side |
| Range trading | ADX < 20, price at a Bollinger band extreme with RSI stretched |
| Liquidity sweep | a wick through a swing point / the Asia range with the close back inside |
| Scalping momentum | 1m/5m: EMA9/21 with a 20-candle breakout, only if the stop is at least 3 spreads |

Every value at a candle uses only candles up to it (a swing point counts once the candles that confirm it
have closed); tests check this by removing later candles. For the latest candles it reports:

- **strategy fit %**: how well each strategy's conditions match the market now, weighted across timeframes,
  as a share of the total (the Assistant page shows it as bars);
- **higher-timeframe bias** from the 1d / 4h / 1h trend and structure;
- **decision** BUY / SELL / WAIT with a **confidence**: active setups vote, weighted by strength, timeframe,
  fit and agreement with the bias. BUY or SELL needs `ASSISTANT_MIN_CONFIDENCE` (default 55%), a setup near
  the robot's timeframe to time the entry, and no opposing bias; otherwise WAIT with the reasons;
- **trade plan**: entry, stop, target of the strongest agreeing setup;
- **how long it usually takes**: the same detector's earlier setups on the same timeframe, measured on candles
  before now: how often the target came first, the average result after the spread, and the minutes to target
  or stop (25th / median / 75th percentile).

These are measurements of past and present prices, not predictions.

How the robot uses it (paper trading):

- `SPARKZ_PRESET=assistant` (entry mode `ASSISTANT`): the assistant's decision opens the trade (one position,
  no grid);
- any other preset with `ASSISTANT_GATE=true` (the default): a rule-based entry is held back unless the
  assistant says the same side;
- backtests have no multi-timeframe feed and run without it. Measure the assistant on history instead:
  `python -m app.assistant.study --source dukascopy --start 2025-01-01` (every BUY/SELL traded with the
  plan's stop, a 2R target, an 8-hour time stop and the real spread; by confidence band, strategy and quarter);
- `GET /api/assistant` and the dashboard's **Assistant** page (summary on the Live page) show the latest
  analysis, refreshed every 30 seconds. With the MT5 bridge it uses all six timeframes (the bridge also sends
  1h / 4h / daily history); with a single-timeframe feed it builds the higher timeframes from it.

### Live prices from MetaTrader 5 (MT5 bridge, read-only)

For a broker that offers MetaTrader 5 (OANDA is not available everywhere), a small script on the computer
running MT5 pushes its quotes to the V2 server. It only reads prices (`symbol_info_tick`, `copy_rates_from_pos`);
a test checks that it contains no order call. The MetaTrader5 Python package runs on Windows only.

1. Pick a long random secret, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Start V2 with the bridge as its data source (same computer, or a server the bridge can reach):

   ```powershell
   $env:MARKET_DATA_PROVIDER = "BRIDGE"
   $env:SPARKZ_BRIDGE_TOKEN = "<secret>"
   cd SPARKZ-TRADER-V2/backend; uvicorn app.main:app --port 8000
   ```

3. With MT5 open and logged in, in another window:

   ```powershell
   pip install MetaTrader5
   $env:SPARKZ_BRIDGE_TOKEN = "<secret>"
   python SPARKZ-TRADER-V2/bridge/mt5_bridge.py --symbol XAUUSD --server http://127.0.0.1:8000
   ```

   `--symbol` is the broker's name for gold (XAUUSD, XAUUSDm, GOLD, ...). MT5 reports times in the broker's
   server time: the bridge detects the offset while the market is open; otherwise pass `--utc-offset 3`
   (server time minus UTC, in hours). At start it sends about two weeks of closed 1-minute candles so the
   indicators are ready at once, then a quote every 2 seconds.

The paper loop then trades the PAPER ACCOUNT on your broker's real bid/ask (`GET /api/bridge/status` shows
what has arrived). `GET /api/bridge/spread` reports your broker's real spread: from every distinct live
quote, and from the spread MT5 records on each 1-minute candle (sent with the start-up history, so it has
numbers even while the market is closed), with the median, 90th/99th percentile and a split by session (UTC),
next to the 0.54 the backtests assume. Rerun a study with your figure, e.g.
`python -m app.research.scalp_study --tfs 15m,1h --spread <median>`. If the quotes stop (terminal closed, weekend), the market shows as closed and new positions
are blocked until they return. The server rejects pushes without the secret, malformed prices, and times in
the future (the usual sign of a wrong `--utc-offset`).

### Orders on your MT5 account: demo or real (opt-in)

By default every order is simulated. With `EXECUTION_MODE=MT5_DEMO` or `MT5_LIVE` the paper loop sends the robot's
approved orders to your own MetaTrader 5 terminal through the bridge (`--trade`):

| | server | bridge | trades |
|---|---|---|---|
| simulation (default) | `EXECUTION_MODE=SIMULATION` | without `--trade` | nothing in MT5 |
| demo account | `EXECUTION_MODE=MT5_DEMO` | `--trade` | demo only; refuses a real account |
| real account | `EXECUTION_MODE=MT5_LIVE` | `--trade --live` (type `I ACCEPT THE RISK`) | real or demo |

```
# server (window 1)
$env:MARKET_DATA_PROVIDER = "BRIDGE"; $env:EXECUTION_MODE = "MT5_DEMO"; $env:SPARKZ_PRESET = "assistant"
$env:SPARKZ_BRIDGE_TOKEN = "<secret>"
python -m uvicorn app.main:app --port 8010
# bridge (window 2, MT5 open and logged in, Algo Trading button ON)
$env:SPARKZ_BRIDGE_TOKEN = "<secret>"
python bridge\mt5_bridge.py --symbol XAUUSDm --server http://127.0.0.1:8010 --trade
```

- A real account trades only when both sides allow it; otherwise the bridge stops at start and the server refuses
  to queue orders. Both re-check the account before every order.
- Each order is at most `MAX_LIVE_LOTS` (default 0.01, hard ceiling 0.1) on the server and `--max-lots` on the
  bridge. Each opening order carries a protective stop loss in MT5 at twice the basket loss limit.
- Daily loss stop: no new trade once the MT5 equity is `LIVE_DAILY_LOSS_PERCENT` (default 3 %) below its first
  value of the UTC day (as first seen by the server that day); closing trades always goes through.
- Orders not collected within 30 s are dropped, never sent late; an order id is never executed twice.
- SPARKZ positions carry their own magic number; positions you open by hand are never touched.
- If MT5 closes the position (protective stop, or you close it in MT5), the robot books the basket as closed
  (`BROKER_CLOSED`).
- STOP ROBOT blocks new orders and cancels uncollected ones; open positions stay in MT5 with their stop.
- The Live page shows the MT5 account (DEMO/REAL, balance, equity, today's change against the daily stop, SPARKZ
  positions) next to the robot's ledger.
- Every study in `reports/` lost money after costs. Run demo first, and real money only with money you can lose.

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

### Long fine-grained history from Dukascopy (free, no account)

Dukascopy publishes free bid/ask history with UTC timestamps. With `datafeed.dukascopy.com` allowed:

```bash
python -m app.cli download --source dukascopy --start 2024-01-01   # stores 1m AND 15m (resampled from the 1m)
python -m app.research.fine_path_study --source dukascopy --path 1m
```

The download fetches two small files per day (bid and ask, about 1,000 days from 2024 to now), stores mid
candles in `data/candles/dukascopy/`, drops flat zero-volume minutes (weekends) and reports the real
spread (median and 90th/99th percentile of the 1m close spread). Without network access to the feed,
export the data from the Dukascopy website ("Historical Data Feed": XAUUSD, 1 minute, one BID and one ASK
CSV file) and import it:

```bash
python -m app.cli import-dukascopy --bid XAUUSD_Candlestick_1_M_BID.csv --ask XAUUSD_Candlestick_1_M_ASK.csv
```

Dukascopy's spread is a bank feed's and is usually tighter than a retail broker's; an MT5 export from
the broker you would actually use shows that broker's spread.

### Market structure and a structure scalping setup (research)

`app/research/structure.py` reads market structure from candles without look-ahead: swing highs/lows (a pivot
counts only once its right-hand candles have closed), trend, break of structure (BOS), change of character
(CHoCH), the Asia session range and liquidity sweeps. `app/research/scalp.py` turns it into a scalping setup:
15m structure bias, a 1m/5m CHoCH in the bias direction (optionally after a liquidity sweep), stop beyond the
pullback, fixed-R target, London/New York hours only, one trade at a time, exits checked on every 1m candle
at the real bid/ask spread (stop assumed when stop and target share a candle).

```bash
python -m app.research.scalp_study --source dukascopy --split 2025-01-01 --spread 0.54
python -m app.research.scalp_study --tfs 15m,1h --min-trades 50    # same setup on 15m/1h (1h/4h bias, wider stops)
```

Every parameter combination is run; the best on the in-sample period (before `--split`) is reported on the
out-of-sample period after it, next to all the others (`reports/studies/scalp_structure_<source>.json`).

`app/research/setup_model.py` adds a model that filters these setups: a shallow gradient-boosting regressor
(scikit-learn) predicts each setup's net R from features known when it appears (session, stop size against
ATR and spread, break strength, momentum, higher-timeframe trend age and slope, Asia range), retrained every
month on earlier trades only (purged: no outcome overlaps the test month). Setups predicted to beat their
costs are traded one at a time and compared with the unfiltered setup on the same months:

```bash
python -m app.research.setup_model --tf 1m --swing-n 3 --variant choch --rr 2.0 --start 2025-01-01
```

### Long fine-grained history from an MT5 export

Where OANDA is not available (it does not serve every country), any broker's MetaTrader 5 history works.
In MT5: View > Symbols (Ctrl+U) > Bars, pick XAUUSD, choose M1 (then M15) from 2024-01-01, Request, then
Export Bars. Import each file into its own `mt5` store:

```bash
python -m app.cli import-csv XAUUSD_M15.csv --source mt5 --timeframe 15m --tz Etc/GMT-2
python -m app.cli import-csv XAUUSD_M1.csv  --source mt5 --timeframe 1m  --tz Etc/GMT-2
python -m app.research.fine_path_study --source mt5 --path 1m
```

`--tz` is the broker server's zone (MT5 exports use server time; many brokers run GMT+2, written
`Etc/GMT-2`, or GMT+3 in summer). If the export has a `<SPREAD>` column, the import reports its median and
90th/99th percentile in price units; pass `--point 0.001` when the broker quotes XAUUSD with 3 digits
(the default 0.01 is for 2 digits).

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
