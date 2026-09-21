# SPARKZ TRADER

A quantitative research, backtesting, and paper-trading platform for FX (starting
with EUR/USD). Built for C-Sparkz Empire.

## What this IS

- A modular pipeline: market data → validation → indicators → regime detection →
  signals → ML probability estimates → risk-managed, cost-aware backtesting →
  paper trading.
- A tool for estimating **probabilities**, not certainties. Every model output is
  framed as "the model estimates an X% probability of [defined event] under the
  current model and data assumptions" — never as "the market will do X."
- Paper-trading only. No real money ever moves through this system.

## What this is NOT

- **Not a guaranteed-profit system.** Nothing in this codebase claims otherwise.
- **Not investment advice.**
- **Not connected to any real broker.** `LIVE_TRADING_ENABLED` defaults to `false`
  and no broker adapter exists in this codebase — the switch exists for a future
  version that would need to fail closed without it.
- **Not proof that any strategy works.** The included baseline strategy and the
  example ML models are demonstrations of the pipeline, not trading advice. On
  the synthetic/random-walk-like data used for local testing, the baseline
  strategy lost money in one live smoke-test run and made money in another
  (different random seeds) — this is expected and is the entire point of
  backtesting honestly rather than cherry-picking a result.

Historical performance does not guarantee future results.

---

## Architecture

```
MARKET DATA (yfinance)
  -> DATA VALIDATION (dedup, sort, invalid-OHLC removal, gap detection)
  -> FEATURE ENGINEERING (EMA/SMA, RSI/MACD, ATR/Bollinger, volume, price structure)
  -> MARKET REGIME DETECTION (TRENDING_UP/DOWN, RANGING, HIGH/LOW_VOLATILITY)
  -> SIGNAL ENGINE (deterministic baseline rules, or ML-probability-derived)
  -> MACHINE LEARNING MODEL (logistic regression / random forest / optional XGBoost)
  -> PROBABILITY / CONFIDENCE (probability_up, probability_down, signal)
  -> RISK ENGINE (position sizing, ATR stops, portfolio-level limits)
  -> BACKTEST ENGINE (event-driven, next-bar execution, spread/slippage/commission)
  -> PERFORMANCE ANALYTICS (Sharpe/Sortino/drawdown/profit factor/etc.)
  -> PAPER TRADING (separate simulated account, no broker credentials)
  -> MONITORING (structured logs, model registry, backtest/trade history in SQLite)
```

## Project layout

```
SPARKZ-TRADER/
├── backend/
│   ├── app/
│   │   ├── main.py, config.py, cli.py
│   │   ├── api/            # FastAPI routes + Pydantic schemas
│   │   ├── data/           # downloader, validator, repository
│   │   ├── indicators/     # trend, momentum, volatility, volume
│   │   ├── features/       # feature engineering pipeline
│   │   ├── strategy/       # baseline rules, regime detection, signal engine
│   │   ├── ml/              # dataset (leak-safe), train, evaluate, predict,
│   │   │                     model_registry, walk_forward
│   │   ├── backtest/       # engine, portfolio, execution, metrics
│   │   ├── risk/           # position sizing, stops, risk manager
│   │   ├── paper/          # paper trading simulator
│   │   ├── database/       # SQLAlchemy models + session
│   │   └── utils/          # logging, time helpers
│   ├── tests/               # pytest suite (59 tests)
│   ├── requirements.txt
│   └── .env.example
├── frontend/                # React + Vite + TS + Tailwind + Recharts dashboard
├── data/                    # downloaded/processed OHLCV (gitignored)
├── models/                  # trained model artifacts (gitignored)
└── reports/                 # generated backtest reports (gitignored)
```

---

## Installation

### Prerequisites
- Python 3.11+
- Node.js 18+ and npm

### Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # adjust values if you want; defaults are sane
```

Optional: `pip install xgboost` if you want the XGBoost model type (not required —
logistic regression and random forest work out of the box).

### Frontend setup

```bash
cd frontend
npm install
```

### Database setup

The SQLite database is created automatically on first run (`init_db()` runs at
API startup). No manual migration step is needed for this version.

---

## Running it

### Backend API

```bash
cd backend
python -m uvicorn app.main:app --reload
```

API docs (interactive): http://localhost:8000/docs
Health check: http://localhost:8000/health

### Frontend dashboard

```bash
cd frontend
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api/*` to the backend
on port 8000 (see `frontend/vite.config.ts`).

### Downloading market data

```bash
cd backend
python -m app.cli download-data --symbol "EURUSD=X" --timeframe 1h
```

This downloads via yfinance, validates/cleans the data, and saves it to
`data/EURUSD_X_1h.parquet`. **Note:** this requires outbound network access to
Yahoo Finance (`query1/query2.finance.yahoo.com`). If your environment blocks
that, the command fails with a clear error rather than crashing — check your
network/firewall settings.

### Running a backtest

Via CLI:
```bash
python -m app.cli backtest --symbol "EURUSD=X" --timeframe 1h --strategy baseline
```

Via API:
```bash
curl -X POST http://localhost:8000/backtest \
  -H "Content-Type: application/json" \
  -d '{"symbol": "EURUSD=X", "timeframe": "1h", "strategy": "baseline"}'
```

Via the dashboard: go to **Backtesting**, adjust the config, click **Run Backtest**.

By default the backtest engine, like paper trading, only holds one position
open at a time (`MAX_SIMULTANEOUS_POSITIONS=1` in `.env`). To let a run hold
several concurrent positions, pass `max_simultaneous_positions` on the
`/backtest` request (or raise `MAX_SIMULTANEOUS_POSITIONS` globally) — the
risk manager's `max_exposure_pct` and `max_drawdown_pct` limits still apply
across all open positions combined.

### Training a model

```bash
python -m app.cli train-model --symbol "EURUSD=X" --timeframe 1h --model-type random_forest
```

Or via the dashboard's **Model Lab** page. This trains on the chronological
first 70% of the data, holds out 15% for validation (used for threshold
selection) and 15% for test (touched only at the end, for reporting).

The label ("did price go up enough to count as a win?") is controlled by two
settings, overridable per-run:
```bash
python -m app.cli train-model --symbol "EURUSD=X" --timeframe 1h --model-type random_forest \
  --lookahead-period 20 --target-return-threshold 0.003
```
- `--lookahead-period` (default `LOOKAHEAD_PERIOD`, 5): how many bars ahead
  the label looks.
- `--target-return-threshold` (default `TARGET_RETURN_THRESHOLD`, 0.0005):
  the minimum future return counted as "up." The default is tuned for
  next-bar noise, not a meaningful move — if your model can't beat chance at
  predicting the very next candle, try widening this before concluding
  there's no learnable signal at all.

Widening the target usually makes the "up" class rarer, which is worth
watching for: `evaluate_classification`'s precision/recall use a fixed 0.5
probability cutoff (`model.predict()`), so a model can have real ranking
ability (a decent ROC AUC) while still showing 0.0 precision/recall simply
because it never crosses 0.5 for a rare class. `train-model` also prints a
**threshold sweep** (validation set only, thresholds 0.30–0.75) showing
precision and signal count at each cutoff — check that before concluding a
model with a reasonable AUC has "no signal."

To backtest a trained model's signals instead of the baseline, pass its
`model_id` as the `strategy` field in a `/backtest` request.

### Checking if an edge is real, not a lucky split

A single train/val/test split can look good (or bad) by chance. Walk-forward
evaluation slides a train/test window across the full history and backtests
each test segment separately, so you can see whether performance holds up
across different time periods:
```bash
python -m app.cli walk-forward --symbol "EURUSD=X" --timeframe 1h --model-type random_forest \
  --train-bars 2000 --test-bars 500
```
Prints per-window metrics plus a summary (`profitable_window_pct`,
`avg_total_return_pct`, best/worst window). A strategy with real edge should
be profitable in most windows, not just win big in one and lose everywhere
else. `--lookahead-period` / `--target-return-threshold` work here too.

### Generating predictions

```bash
curl -X POST http://localhost:8000/models/predict \
  -H "Content-Type: application/json" \
  -d '{"model_id": "<your_model_id>", "symbol": "EURUSD=X", "timeframe": "1h"}'
```

Returns `probability_up`, `probability_down`, `signal`, and a plain-language
explanation — never a bare directional claim.

### Starting paper trading

```bash
curl -X POST http://localhost:8000/paper/start \
  -H "Content-Type: application/json" \
  -d '{"account_name": "default", "starting_balance": 10000}'
```

Then check `/paper/account`, `/paper/positions`, `/paper/trades`. There is no
scheduler/loop wired up in this version to automatically feed live candles into
the paper simulator — see "Known limitations" below.

### Running tests

```bash
cd backend
pytest
```

59 tests covering: data validation, indicator correctness (including an
explicit look-ahead-bias check), signal rules, position sizing, stop/target
calculations, the backtest engine's next-bar execution rule and cost model,
ML dataset construction and chronological splitting, a synthetic leakage
injection test, walk-forward evaluation, the paper trading simulator, and a
full API smoke-test suite.

---

## Risk warnings (read this)

- **Overfitting**: it is easy to make a backtest look good by tuning parameters
  against the same data you're testing on. This build does NOT auto-optimize the
  baseline strategy's thresholds, and ML models are trained/validated/tested on
  strictly separated chronological splits specifically to guard against this —
  but you can still defeat these protections by, e.g., repeatedly retraining and
  cherry-picking the model with the best test-set score. Don't do that; use the
  validation set for that kind of iteration and look at the test set once.
- **Survivorship bias**: EUR/USD as a pair doesn't "delist," but if you extend
  this to equities, be aware that historical universes often exclude companies
  that went bankrupt or were delisted, inflating backtested returns.
- **Look-ahead bias**: guarded against via causal-only indicator windows, an
  explicit forbidden-columns list, and shifting signals forward one bar before
  execution — see `app/backtest/engine.py`'s module docstring and
  `tests/test_indicators.py::test_indicators_do_not_use_future_data`.
- **Data leakage**: guarded against via `app/ml/dataset.py`'s `LeakageError` and
  strict chronological splitting — see `tests/test_ml_dataset.py`.
- **Regime change**: a model trained on one market regime (e.g. a trending
  period) may perform very differently in another (e.g. ranging or high-vol).
  The walk-forward evaluator (`app/ml/walk_forward.py`) exists specifically to
  surface this — expect inconsistent per-window results; that's the market
  being non-stationary, not a bug.
- **Transaction costs, slippage, liquidity**: modeled via configurable
  spread/slippage/commission in the backtest engine, but real-world execution
  can still differ, especially in fast markets or on illiquid instruments.
- **Model decay**: even a genuinely useful model's edge can fade over time as
  market conditions change. Retrain and re-validate periodically; don't treat
  a trained model as permanent.

A model with high classification accuracy is **not automatically** a profitable
trading strategy — see `app/ml/evaluate.py`'s module docstring. Always check
the backtested trading metrics, not just accuracy/F1/ROC-AUC.

---

## Known limitations (v1)

- **No live broker integration** — by design, for this version. `LIVE_TRADING_ENABLED`
  is a hard-coded-false safety switch for a future adapter that doesn't exist yet.
- **4h timeframe** is listed in config but yfinance doesn't natively support a 4h
  interval — you'd need to resample from 1h data yourself (not implemented).
  This is called out in `app/data/downloader.py`.
- **Walk-forward evaluation** uses fixed train/test bar counts, not fully
  configurable expanding windows (only rolling, non-overlapping by default).
- **No authentication** on the API — fine for local development, not
  production-ready as-is.
- This was tested end-to-end with synthetic OHLCV data (since the development
  sandbox couldn't reach Yahoo Finance's servers). Run `download-data` yourself
  against real EUR/USD history before trusting any specific numbers.

## What should be built next

1. A broker adapter interface (behind `LIVE_TRADING_ENABLED`) with a mock/local
   implementation first, real broker integration only after extensive paper
   trading validation and with explicit, separate user consent.
2. Additional markets (GBP/USD, USD/JPY, crypto pairs) — the architecture
   supports this already via the `symbol`/`timeframe` parameters throughout,
   but each asset class's behavior (24/7 crypto markets vs. FX sessions, equity
   corporate actions, etc.) should be validated before assuming it "just works."
3. Configurable expanding-window walk-forward evaluation, not just fixed rolling windows.
4. Native 4h resampling from 1h data.
