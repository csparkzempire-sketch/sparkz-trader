# SPARKZ Adaptive Grid & Basket Trading System

A quantitative research and simulation system for grid / basket trading
strategies. It models, backtests, stress-tests and paper-trades the cycle:

```
market analysis -> initial entry -> monitor -> add positions when conditions are met
-> manage the basket's P&L -> close the basket -> re-analyse -> new cycle
```

**This is not a profit robot.** Version 1 has no broker connection and cannot
place real orders (see [Safety](#safety)). Its job is to find out whether such
a strategy has a robust statistical edge, and how it fails, before anyone
thinks about real money. The findings so far are in
[reports/TECHNICAL_REPORT.md](reports/TECHNICAL_REPORT.md).

## Quick start

```bash
cd backend
pip install -r requirements.txt
python -m app.cli download --all                        # Yahoo data for all 5 markets, 15m and 1h
python -m app.cli backtest --preset B_atr_grid          # XAUUSD 15m by default
python -m app.cli compare                               # strategies A-E + control, side by side
python -m app.cli stress --preset B_atr_grid
python -m app.cli robustness --preset B_atr_grid --runs 60
python -m app.cli sensitivity --preset B_atr_grid
python -m app.cli walk-forward --preset B_atr_grid --timeframe 1h
python -m app.research.study                            # the full study behind the technical report
python -m pytest                                        # 108 tests

uvicorn app.main:app --port 8100                        # research API (binds to localhost)
cd ../frontend && npm install && npm run dev            # dashboard on http://localhost:5174
```

Any setting can be changed per run with `--set dotted.path=value`, for example
`--set grid.atr_multiplier=0.8 sizing.mode=LINEAR risk.max_positions=3`, or
for every run through `config/default.yaml` and the flat names in `.env.example`.

## Data

| Source | What you get |
|---|---|
| `download` (Yahoo Finance) | about 60 days of 15m bars, about 2 years of 1h bars; 4h is resampled from 1h |
| `import-csv FILE --symbol .. --timeframe .. --tz ..` | MT5 exports (with per-bar spread) or any OHLC CSV |

Every download is added to the SQLite store (`data/sparkz_grid.db`), so 15m
history grows over time. **XAUUSD from Yahoo is COMEX gold futures (GC=F)**, a
proxy for spot gold; import broker data for a faithful spot test. All data
passes the validator (UTC, sorted, de-duplicated, consistent OHLC, forming
candle removed).

## How the strategy works

1. **Analysis** (`strategy/market_analysis.py`, `regime_detector.py`): EMA 20/50/200, RSI,
   ATR, MACD, Bollinger Bands, ADX, realized volatility, recent return, candle structure,
   distance from the EMAs. Each bar gets a trend regime (TRENDING_UP, TRENDING_DOWN,
   RANGING, UNCERTAIN) and a volatility regime (HIGH, NORMAL, LOW, from a trailing
   percentile of ATR%). Every value uses past bars only.
2. **Entry** (`entry_engine.py`): TREND mode buys when EMA20 > EMA50, close > EMA20,
   RSI > 50 and ADX >= 20 (mirror for sells); RANGE_FADE fades Bollinger/RSI extremes
   in ranging markets. No trade in UNCERTAIN or warm-up bars, nor (by default) in high
   volatility. The signal forms at a bar's close and fills at the next bar's open.
3. **Grid** (`grid_engine.py`): A = fixed price step (optionally growing), B = ATR ×
   multiplier, C = ATR step that also needs the analysis to still back the direction.
   Averaging grids add when price moves against the basket; pyramiding adds only when it
   moves in favour.
4. **Sizing** (`position_sizing.py`): FIXED, LINEAR, PYRAMID, MARTINGALE. Martingale is
   off by default, labelled HIGH RISK and refused unless `allow_martingale` is set.
   A loss never makes the next basket bigger.
5. **Basket** (`basket_manager.py`): all positions share one P&L, average entry, MAE/MFE,
   exposure and margin. Target: fixed USD, % of equity, risk/reward, or ATR distance.
   Loss limit: % of equity at the basket's start.
6. **Risk** (`risk/risk_manager.py`): every position must pass the risk engine first:
   max positions (hard ceiling 20), daily loss, cooldown after a losing basket,
   exposure (leverage) and margin limits. The account drawdown limit closes everything
   and halts until a person resumes it.
7. **Reset**: after a basket closes, the next one needs a fresh signal from a later bar.

## Backtest realism

- Event-driven, bar by bar, the same engine for backtests and paper trading.
- Spread (instrument default, per-bar from MT5 data, or overridden), slippage on every
  fill, commission per lot per side, optional entry latency.
- Adds, targets and stops trigger at exact price levels inside the bar. When one bar
  could have hit them in more than one order, the default PESSIMISTIC mode simulates
  both orders and keeps the one that leaves less equity at the close.
- Gaps fill at the worse price for stops and adds.
- MAE/MFE, notional exposure, effective leverage and margin are recorded for every basket.

## Research tools

- **Comparison lab**: strategies A-E plus a single-position control, run separately on
  identical data and costs. Nothing is combined or ranked.
- **Stress tests**: strong trends up and down, sideways, a volatility spike, a long
  one-way move, 3× spread, 5× slippage with latency, worst-order losing streak, repeated
  re-entries, and the cost of a full basket.
- **Monte Carlo**: basket-order permutation and bootstrap; re-simulation with random
  spread, slippage, latency and parameter changes.
- **Sensitivity**: grid spacing, sizing mode, max positions, target, loss limit, spread.
- **Walk-forward**: parameters chosen in-sample, judged out-of-sample; plus period-by-period stability.
- **ML (optional, later phase)**: P(profitable basket | conditions at the signal bar),
  chronological splits with a purge, compared with base rates.

## Layout

```
backend/app/
  config.py            settings, validation, presets, fail-closed live trading
  data/                instruments, downloader, CSV import, validator, SQLite repository
  strategy/            market analysis, regimes, entry, grid, sizing, basket manager
  risk/                risk manager, exposure, drawdown
  backtest/            engine, execution costs, portfolio, metrics, comparison, stress,
                       Monte Carlo + sensitivity, walk-forward, studies
  paper/               paper simulator, broker seam (simulated only)
  research/            ML study, full study runner
  models/              SQLite tables, API request schemas
  api/                 FastAPI routers: market, strategy, backtest, baskets, paper, risk
config/                default.yaml, strategies/*.yaml (A-E + control)
frontend/              React + Vite + TypeScript + Tailwind + Recharts dashboard
reports/               TECHNICAL_REPORT.md and generated study JSON
```

## Safety

- `LIVE_TRADING_ENABLED` / `LIVE_TRADING` = true is rejected at configuration load, so
  the API won't start. `paper/broker.py` only has a simulated broker, and asking for a
  live one raises. There is no order-sending code.
- No API keys exist in this project; a future broker adapter would read credentials from
  server-side environment variables, never from frontend code.
- The API has no authentication and allows CORS only from localhost: run it on your own
  machine, not on a public address.
- Past performance in backtests or paper trading does not predict future results.
