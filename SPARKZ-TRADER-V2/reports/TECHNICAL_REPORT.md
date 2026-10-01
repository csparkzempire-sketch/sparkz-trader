# SPARKZ TRADER V2: Technical Report

*Market data + adaptive grid & basket simulator. Report date: 2026-10-01.*

Everything below is **simulation**: historical replays and synthetic scenarios, with simulated execution against a
PAPER ACCOUNT. No real order was ever placed, and nothing here can place one.

Results are stated as "configuration X produced Y over period Z under assumptions A". They are not forecasts.
Historical results, paper results, live market conditions and real execution are four different things.

Study outputs: `reports/studies/*.json`, produced by `python -m app.research.studies`.

---

## Summary of findings

1. **The grid presets' backtest results are driven mainly by an assumption, not by the market.**
   OHLC candles do not say whether the high or the low came first inside a candle. That order decides whether a
   tight grid "buys the dip and sells the rebound" inside one candle.
   - On the same 2 months of XAUUSD 15m data, `video_style` ranges from **−$1,751 (stopped by the 20% drawdown
     halt)** to **+$4,194**, depending only on that assumption.
   - Using real 5m candles as the intrabar path narrows the range, but does not remove it.
2. **Every grid preset has the typical grid profile.** Win rates are 89–97%. Average win is about $10, average
   loss about $180–200. One losing basket erases about **20 average wins**. About a quarter of winning baskets sat
   through an open loss larger than 3× the profit they finally made.
3. **The grid presets are fragile to costs.** On the base period:
   - spread ×3 turns the ATR grid from +$985 to −$724;
   - spread ×3 plus slippage ×5 takes it to −$1,362.
4. **Walk-forward on 2.3 years of 1h data:** the ATR grid lost money in **all 4** out-of-sample test folds
   (average −6.7% per fold; worst test drawdown −19%).
   - The single-position baseline made small gains in 4 of 4 folds (+0.3% to +1.5%), always far below buy-and-hold
     in the rising folds.
   - On the 15m data (3 short folds) the ATR grid made +1.4% on average out-of-sample; `video_style` lost −1.4%.
     These folds are 7–11 days long and too short to judge.
5. **Synthetic stress:** the ATR grid loses money on average in normal (random walk), high-volatility, reversal
   and news-spike markets. It profits in steady trends, the regime where its entries follow the trend.
6. **Critical failure scenario:** a basket is open when price moves 6.3% against it without a bounce.
   - The basket loss limit caps the damage at about −$200 (2% of equity).
   - Without the limit, the same move costs −$828 (ATR grid) or −$2,068 (`video_style`, which then trips the
     20% account-drawdown halt).
7. Building V2 exposed **four ways a backtest can flatter a grid**. Each is fixed and covered by tests:
   - intrabar order (§9.2);
   - latency modelled by path interpolation (§9.4);
   - a risk refusal that skipped the loss-limit check (§7);
   - a thesis check too slow to ever trigger (§4).

**Conclusion:** on the data available, no grid configuration here shows evidence of a robust edge.
- The positive in-sample numbers depend on the intrabar-order assumption, low costs, or both.
- The longest out-of-sample test (1h, 2.3 years) is negative in every fold.
- The system is built to make these failures visible, and it does.

---

## 1. Architecture

```
MarketDataProvider ─▶ MarketEngine ─▶ AdaptiveGridBasketStrategy ─▶ OrderIntent ─▶ RiskManager ─▶ SimulationExecutor
 mock / historical /    indicators,     analysis → entry → grid      OPEN / ADD /    pre-trade      spread, slippage,
 Yahoo / OANDA (read)   regime          → basket → target/limit      CLOSE           gate           commission, delay
                                              ▲                                                         │ Fill
                                              └──────────── on_fill ◀── Robot ◀─────────────────────────┘
                                                                          │
                                                   PaperAccount · EventLog · SQLite · WebSocket · dashboard
```

- **Separation.** The strategy only *decides*. It returns `OrderIntent`s and is told about fills afterwards.
  - It never imports a provider, a broker client or the executor; `tests/test_safety_and_risk.py` checks this by
    parsing the imports.
  - The broker provider has only GET endpoints for pricing, candles and instruments.
  - The only working execution adapter is `SimulationExecutor`. `LiveExecutionAdapter` raises on construction.
- **One engine.** `engine/robot.py::Robot` is used unchanged by both the backtest (`backtest/engine.py`) and paper
  trading (`paper/runner.py`).
  - A test checks that the paper engine's rolling-window indicator state equals the backtest's precomputed state
    on the same candle.
- **Providers** implement `get_latest_tick`, `get_candles` (closed candles only), `get_symbol_info`,
  `get_spread`, `get_market_status` and `subscribe_to_market_data`.
  - Mock (9 scenarios, simulated clock)
  - Historical (refuses bars beyond its cursor)
  - Yahoo (delayed; gold = GC=F future)
  - OANDA v20 (read-only; credentials from env only; token hidden in `repr` and errors)
- **Environments:** DEVELOPMENT, BACKTEST, PAPER. Setting `LIVE_TRADING_ENABLED=true` is rejected by validation.
- **Persistence:** SQLite via SQLAlchemy. Tables: runs, events, baskets, fills, equity. A failing database is
  counted in health, and never stops the robot.
- **API / UI:**
  - FastAPI routes for market, strategy, baskets, account, system and backtest, plus `/ws/live`.
  - React dashboard: Live, Event log, Backtest, Stress, Walk-forward, Parameter lab, System.
  - The always-visible **STOP ROBOT** button was verified in a browser: it stopped and then resumed the robot.

## 2. How the video-style strategy was modelled

`VIDEO_STYLE_MODE=true` loads `config/presets/video_style.yaml`:
- XAUUSD 15m;
- FIXED grid distance 2.0;
- LINEAR lots (0.01, 0.02, … 0.05);
- $10 basket target;
- max 5 positions;
- 2% basket loss limit.

These values reproduce the behaviour described in the reference video: small fixed spacing, growing size and a
small money target for the whole basket. **The video's actual rules are not public, and this is not its
algorithm.** The dashboard and presets say so wherever the mode appears. Entries reuse the documented baseline
trend rules, because the video does not specify its entry logic.

## 3. Entry logic

`strategy/analysis.py` + `strategy/entry_engine.py`, evaluated on **closed candles only**.

- **Direction** comes from three core rules:
  - BUY when EMA20 > EMA50, close > EMA20 and RSI > 50;
  - SELL is the mirror image.
- **Rule agreement** ("confidence") is the share of 7 directional checks that agree: the 3 core rules plus MACD
  histogram, EMA200 side, ±DI and recent return. It is labelled everywhere as *not a probability*.
- **Decision values:**
  - WAIT: warming up, high-volatility filter, uncertain regime, ADX < 20, agreement < 0.6, or optional EMA200
    misalignment.
  - NO_SIGNAL: the core rules disagree.
  - BUY / SELL: all conditions met.
- **Regime** (`market/regime.py`): WARMUP > HIGH_VOLATILITY > TRENDING_UP / TRENDING_DOWN / RANGING >
  LOW_VOLATILITY > UNCERTAIN, from ADX, ±DI, EMA slope and the volatility percentile.
- **Optional `RANGE_FADE` mode:** Bollinger band extremes in a RANGING market.

## 4. Grid logic

`strategy/grid_engine.py`. Re-entry levels are measured from the **last entry's trigger price**:

| Mode | Spacing | Notes |
|---|---|---|
| FIXED | `GRID_DISTANCE` price units | |
| ATR | `GRID_ATR_MULTIPLIER` × ATR at the last closed bar | spacing follows volatility |
| SIGNAL_CONFIRMED | ATR spacing | adds stop for good once a **closed bar** breaks the entry thesis |
| NONE | no adds | baseline |

**SIGNAL_CONFIRMED thesis:** the thesis breaks on a close beyond EMA50, an EMA20/50 cross, or an opposite trend
regime.
- *Finding:* the first version (EMA cross only) never triggered before the grid was full. With adds allowed
  inside one fast candle, a 0.5-ATR grid fills in one or two bars.
- The preset therefore also requires one closed bar between adds (`min_bars_between_adds: 1`).

**Path handling.** Within one tick, if the path crosses several levels (add, then loss limit, say), the level
reached first acts first. The strategy is called again for the rest of the path.

**Fill prices:**
- a level crossed continuously fills at the level;
- a level jumped over (candle-open gap, live poll) fills at the current price.

**Adds stop when:**
- max positions reached;
- `MAX_BASKET_DRAWDOWN` reached;
- the exposure or margin limit would be exceeded;
- the thesis breaks (SIGNAL_CONFIRMED);
- data is stale;
- EMERGENCY STOP is active.

## 5. Position-sizing logic

`strategy/position_sizing.py`, per entry number n:

| Mode | Lots for entry n |
|---|---|
| FIXED | base |
| LINEAR | n × base |
| PYRAMID | base, but adds only in the basket's favour |
| MULTIPLIER | base × mⁿ⁻¹ |

- **MULTIPLIER is HIGH RISK.** It is refused (`PermissionError`) unless `ALLOW_MULTIPLIER_SIZING=true`, and it is
  never the default.
  - It is listed in `high_risk_settings` and flagged in the UI.
  - In the critical scenario its exposure reached $81.7k on $10k equity, 6.2× the FIXED grid's.
- Sizes never depend on the previous basket's result: there is no martingale across baskets.

## 6. Basket logic

`strategy/basket_manager.py`. All positions opened in one cycle form one basket, which is managed and closed as a
whole.

- **Metrics tracked:**
  - average entry (fill prices, so costs are included);
  - floating P&L at the executable exit price (bid for BUY / ask for SELL, plus slippage), net of commissions;
  - target and loss-limit prices;
  - MAE, MFE, peak P&L and basket drawdown;
  - max lots, notional and margin.
- **Targets:**

  | Mode | Target |
  |---|---|
  | FIXED_PROFIT | USD |
  | PERCENT_EQUITY | % of equity at the basket's start |
  | ATR_TARGET | ATR × multiplier beyond the average entry |
  | RISK_MULTIPLE | loss limit × multiple |

- **Loss limit:** `MAX_BASKET_LOSS_PERCENT` of equity at the basket's start (and/or a USD cap). Every basket must
  have one (validation).
- **Recovery analysis** is *analytical only*: the move needed to break-even, target or loss limit (in price and
  ATR), and the extra lots that would bring break-even within 1 ATR. Nothing is ever added because of it.
- **Close → record → reset:**
  - the completed basket is stored and the account realizes P&L;
  - **cooldown**: the bar the basket closed in can never open a new one, then `COOLDOWN` more bars, plus
    `COOLDOWN_AFTER_LOSS` after a loss;
  - a new basket needs a fresh signal. No immediate re-entry after a win (tested).

## 7. Risk logic

`risk/risk_manager.py` is a pre-trade gate on every OPEN and ADD order. CLOSE orders are never blocked.

- **Refusal codes, in check order:**
  - STOPPED
  - DATA_STALE
  - POSITION_LIMIT (configurable, hard ceiling 20)
  - BASKET_DRAWDOWN
  - NO_EQUITY
  - DAILY_LOSS
  - COOLDOWN
  - EXPOSURE (notional / equity)
  - MARGIN (margin / equity)
- **On every tick:** account drawdown ≥ `MAX_ACCOUNT_DRAWDOWN` → close the basket and **halt** until a person
  resumes. This protection also acts during an emergency stop (tested).
- **EMERGENCY STOP** (button or API):
  - no new entries, no adds, no strategy processing;
  - pending simulated orders cancelled;
  - positions preserved and still marked to market.
- **Stale data** (paper): no price change for `STALE_AFTER_SECONDS`, a live feed's tick that old, or a closed
  market → no new positions. Open baskets keep being managed.
- *Bug found and fixed.* When the nearest level on a price path was a grid add and the risk gate refused it
  (e.g. DAILY_LOSS), the robot stopped processing that tick. A loss limit further along the same path was
  skipped. The robot now asks the strategy again after a refusal (`test_daily_loss_limit_blocks_new_baskets`).

## 8. Execution simulator

`execution/fill_engine.py` + `execution/simulator.py`:
- BUY fills at ask + slippage; SELL at bid − slippage.
- Spread: live quote, instrument default (XAUUSD 0.30) or override, × `SPREAD_MULTIPLIER`.
- Slippage: instrument default (XAUUSD 0.05), × `SLIPPAGE_MULTIPLIER`.
- Commission per lot per side.
- **Delay:** orders queue until `now + EXECUTION_DELAY_MS`. Paper trading fills at the first real tick after
  the delay. For the backtest, see §9.4.
- No partial fills, requotes or swap / overnight financing (see §13).

## 9. Backtesting methodology

### 9.1 Mechanics

- `HistoricalDataProvider` serves candles up to a cursor and refuses anything beyond it.
- Indicators are computed with backward-looking formulas only. A test truncates the series and checks that every
  earlier row is unchanged.
- **Decisions happen only at candle close.** Inside each candle the robot sees a price path.
- **Walk-forward windows** trade only inside their own dates. Indicators warm up on the preceding data.
- **End of data:** an open basket is closed at the last price and labelled `END_OF_DATA`.
- **Determinism:** the same inputs and seed give identical results.

### 9.2 Intrabar order (the dominant assumption)

The default synthetic path is open → extreme → extreme → close. While a basket is open, `INTRABAR_ORDER` decides
which extreme comes first:
- `FAVOURABLE_FIRST` (default)
- `RANDOM`
- `ADVERSE_FIRST`

*"Adverse first" looks pessimistic but flatters grids:* the grid adds at the dip and takes the target on the
rebound within one candle. On a driftless random walk (8 seeds) it turned the expected cost-driven loss into a
profit (+$249 mean vs −$387 for favourable-first). The default is therefore `FAVOURABLE_FIRST`, and every study
reports all three.

**Net P&L, XAUUSD 15m, 2026-07-29 → 2026-10-01** (Yahoo GC=F, 4,156 candles; buy-and-hold +4.5%; $10k start):

| Preset | Favourable first | Random | Adverse first | Max DD (fav.) | Loss-limit closes (fav.) |
|---|---|---|---|---|---|
| baseline (1 position, $10/$10) | +$54 | +$14 | −$56 | −1.2% | 115 of 237 |
| fixed_grid | +$475 | +$2,089 | +$2,226 | −8.9% | 11 |
| atr_grid | +$985 | +$1,526 | +$2,966 | −10.1% | 9 |
| signal_grid | +$744 | +$957 | +$793 | −7.5% | 8 |
| video_style | **−$1,751 (halted)** | +$1,202 | +$4,194 | −18.9% | 17 |
| multiplier_high_risk | **−$1,918 (halted)** | +$1,899 | +$3,618 | −20.2% | 17 |

Points to note:
- The baseline barely depends on the assumption: one position, symmetric target and stop.
- `signal_grid` depends on it least of the grids: at most one add per closed bar.
- `video_style` and multiplier sizing depend on it most.

### 9.3 Resolution check with real 5m candles

From 2026-08-27 (start of the 5m data) to 2026-10-01, a −10.2% fall in gold, the 15m decisions were re-run with
**real 5m candles as the intrabar path**:

| Preset | 15m OHLC path (fav. / random / adverse) | 5m path (fav. / random / adverse) |
|---|---|---|
| atr_grid | +1,253 / +1,267 / +1,890 | +1,311 / +1,197 / +981 |
| signal_grid | +790 / +1,044 / +1,032 | +1,014 / +1,023 / +1,033 |
| video_style | −1,235 / +883 / +2,630 | +10 / +1,407 / +2,580 |
| multiplier_high_risk | −596 / +1,322 / +2,210 | −10 / +786 / +868 |
| baseline | +74 / +54 / +54 | +74 / +54 / +54 |

- The 5m path stabilizes the ATR-spaced grids (about +$1.0k–1.3k in every ordering).
- `video_style` (2.0 spacing on a $4,200–4,700 instrument) still swings from +$10 to +$2,580: its grid is finer
  than even 5m candles resolve. Judging it would need tick or 1m data over a long period. Only 4 days of 1m data
  were available.

### 9.4 Latency

*Two flawed models were found and replaced before reporting:*
- **Flaw 1:** filling at the next path point (up to 5 minutes later, at the candle extreme). This made all
  presets lose about $2,000 at 60 s.
- **Flaw 2:** interpolating along the synthetic path. This made 60 s of latency *profitable* for grids, because
  the path keeps moving the same way after every level cross.

**Final model:**
- the backtest inserts a price exactly where the path crosses a level, so a delayed order starts there;
- it fills at that price plus zero-mean noise (ATR × √(delay / candle length));
- averaged across scenarios and seeds this gives no systematic gain (tested);
- one 60 s run per preset is reported in §10.2, and its sign is within noise.

### 9.5 Report contents

Every backtest report contains:
- P&L and return;
- win rate, average win and loss, largest loss, profit factor, expectancy;
- max drawdown (% and $), worst floating P&L, losing streaks;
- positions per basket, max lots, notional, effective leverage, margin;
- time in market, costs paid (spread / slippage / commission);
- buy-and-hold over the same candles;
- MAE/MFE analysis (largest loss in average wins, winners that sat through >3× their profit);
- breakdowns by regime, direction and close reason;
- monthly P&L, the P&L distribution and the assumptions list.

**Profile, favourable-first, 15m:**

| Preset | Win rate | Avg win | Avg loss | Worst basket = N avg wins | Winners with MAE > 3× profit | Max notional | Max margin |
|---|---|---|---|---|---|---|---|
| baseline | 51% | $10.0 | −$10.0 | 1.0 | 0% | $4.7k | 0.5% |
| atr_grid | 97% | $10.5 | −$203 | 20.2 | 25% | $23.7k | 2.5% |
| video_style | 90% | $10.0 | −$183 | 20.3 | 24% | $70.9k | 8.6% |
| multiplier_high_risk | 88% | $10.0 | −$180 | 20.2 | 25% | $70.9k | 8.7% |

By regime at entry (atr_grid): TRENDING_UP baskets +$860 (128); TRENDING_DOWN +$125 (150).
`video_style` lost in both regimes: −$325 (up) and −$1,426 (down).

## 10. Stress-test results

### 10.1 Synthetic market scenarios

3,000 15m bars, mean of 3 seeds, $10k start. "Full grids" = baskets that reached max positions.

| Scenario | atr_grid mean | worst DD | full grids | loss-limit closes | video_style mean | worst DD | halts |
|---|---|---|---|---|---|---|---|
| normal (random walk) | −$51 | −6.1% | 116 | 16 | +$37 | −8.7% | 0 |
| trend_up | +$3,053 | −2.0% | 94 | 2 | +$1,962 | −5.6% | 0 |
| trend_down | +$1,665 | −2.1% | 90 | 1 | +$1,560 | −2.7% | 0 |
| sideways | +$404 | −4.6% | 96 | 3 | **−$809** | −12.4% | 0 |
| high_vol (3×) | **−$1,757** | **−20.0%** | 154 | 49 | **−$1,938** | **−20.2%** | 3 of 3 |
| spike (8 ATR) | +$259 | −5.7% | 121 | 12 | +$51 | −10.8% | 0 |
| reversals | **−$1,280** | −17.5% | 106 | 50 | +$164 | −8.8% | 0 |
| news_spikes | **−$1,216** | −16.4% | 123 | 35 | **−$907** | −20.1% | 1 |
| adverse_trend (one-way up) | +$5,830 | −0.3% | 1 | 0 | +$6,383 | −2.0% | 0 |

- `adverse_trend` is profitable because the trend-following entries go *with* it. The scenario is adverse only to
  a basket opened against it, which is what the critical failure scenario (§12) forces.
- Strong steady trends are the only clearly favourable conditions. Random, volatile, reversing and shock markets
  lose. The high-volatility runs hit the 20% account halt.

### 10.2 Execution stress on real XAUUSD 15m (favourable-first)

| Preset | Base | Spread ×3 | Slippage ×5 | Both | 60 s latency* |
|---|---|---|---|---|---|
| baseline | +$54 | −$109 | −$96 | −$182 | +$80 |
| atr_grid | +$985 | **−$724** | +$25 | **−$1,362** | +$14 |
| fixed_grid | +$475 | −$373 | +$150 | −$217 | +$684 |
| signal_grid | +$744 | +$102 | +$329 | +$266 | +$1,094 |
| video_style | −$1,751 | −$1,866 | −$1,641 | −$1,776 | −$1,854 |

\* One noise draw each (see §9.4). Differences of a few hundred dollars are within noise.

## 11. Walk-forward results

Chronological rolling folds of train → validation → test.
- Parameters are chosen on **validation** from at most 4 hand-picked candidates.
- Each test window is used once.
- Only test rows are out-of-sample.

| Study | Folds | Avg test return | Profitable test folds | Worst test DD | Test baskets |
|---|---|---|---|---|---|
| atr_grid, 1h, 2024-06 → 2026-10 (spacing 0.5/1.0 ATR × max pos 3/5) | 4 | **−6.7%** | **0 / 4** | −19.0% | 599 |
| baseline, 1h, same period | 4 | +0.9% | 4 / 4 | −1.0% | 510 |
| atr_grid, 15m (same space) | 3 | +1.4% | 2 / 3 | −3.2% | 107 |
| video_style, 15m (distance 2 / 4) | 3 | −1.4% | 1 / 3 | −6.5% | 147 |

**1h test folds** (strategy vs buy-and-hold):

| Test fold | atr_grid | baseline | Buy-and-hold |
|---|---|---|---|
| 2025-06-30 → 2025-10-21 | −3.7% | +0.6% | +26.9% |
| 2025-10-21 → 2026-02-18 | −3.5% | +1.2% | +16.8% |
| 2026-02-18 → 2026-06-10 | −4.8% | +1.5% | −17.0% |
| 2026-06-10 → 2026-10-01 | −14.9% | +0.3% | +1.9% |

- The chosen parameters changed between folds: no stable best setting.
- **Parameter lab (15m, atr_grid; 6 candidates: spacing 0.5/1.0 × target $5/10/20):**
  - validation chose spacing 1.0 ATR with a $20 target;
  - on the untouched test period (2026-09-21 → 10-01) that made +2.1% on 24 baskets, with buy-and-hold −4.2%;
  - train/validation rank correlation was 0.83;
  - one 10-day period with 24 baskets proves nothing. It is reported as a single observation.

## 12. Failure scenarios

**Critical failure scenario** (`backtest/stress_test.py::critical_failure`, synthetic). The setup:
- the strategy is run on an uptrend until it holds a basket;
- from that bar, price falls 6.3% over 80 bars without a meaningful bounce;
- the full bar-by-bar path is in the report and the Stress page, including floating P&L, exposure, margin,
  positions, break-even distance, MAE and account drawdown.

| Variant (atr_grid unless noted) | Outcome | Realized | Worst floating | Max exposure | Max margin | Break-even distance | Account DD | Bars open |
|---|---|---|---|---|---|---|---|---|
| as configured | LOSS_LIMIT | −$204 | −$194 | $13.2k | 1.3% | 10.2 ATR | 2.1% | 19 |
| no close at loss limit | still open at end | **−$828** | −$828 | $13.2k | 1.3% | **44.5 ATR** | 8.2% | 81 |
| LINEAR sizing | LOSS_LIMIT | −$197 | −$152 | $39.5k | 4.1% | 2.3 ATR | 5.2% | 7 |
| MULTIPLIER ×2 (HIGH RISK) | LOSS_LIMIT | −$205 | −$191 | **$81.7k** | 8.1% | 1.4 ATR | 2.0% | 6 |
| video_style, no close at limit | ACCOUNT_DRAWDOWN halt | **−$2,068** | −$2,013 | $39.6k | 4.6% | 39.3 ATR | **20.2%** | 68 |

- Larger sizing reaches the money loss limit sooner, in fewer bars, so its realized loss is no bigger. Its
  exposure, margin and drawdown along the way are 3–6× higher.
- Without a hard basket limit, a grid's loss grows with the trend. Here it was stopped only by the account halt.

**Other failure modes covered by tests:**
- a gap through the loss limit (realized loss is larger than the limit and shown as such);
- stale data;
- provider errors (logged and throttled, never fatal);
- the account halt;
- emergency stop with pending orders.

## 13. Known limitations

- **Data.**
  - Gold is the Yahoo GC=F future, a proxy for spot XAUUSD (contract rolls, different hours).
  - 15m covers about 2 months; 5m about 5 weeks; 1m only 4 days.
  - There is no real bid/ask history: spread is a constant 0.30 (× stress multipliers).
  - The OANDA provider is implemented and tested against a fake client, but **was not run against a real
    account** in this environment (no credentials).
- **Intrabar path.** The order of highs and lows is unknown, and grid results depend on it heavily (§9.2–9.3).
  Tick data would be needed to resolve tight grids.
- **Execution.** No partial fills, requotes, stop-outs, weekend swap or financing. A gap is filled at the next
  price with normal spread; real spreads widen at gaps and news.
- **Latency model** (backtest) is a statistical approximation (§9.4).
- **Sample size.** The 15m windows give 7–11-day walk-forward folds. The 1h run is longer, but the presets were
  designed for 15m.
- **Paper persistence.** A restart begins a new paper account. Earlier sessions stay in SQLite but are not resumed.
- **No ML.** scikit-learn was in the proposed stack, but V2 has no ML component, so it is not a dependency.
- **"VIDEO_STYLE_MODE"** is an approximation from a description, not the video's code.

## 14. Setup instructions

See `README.md` for full details. In short:

```bash
cd SPARKZ-TRADER-V2
cp .env.example .env                       # never commit .env; real env variables take precedence
cd backend && pip install -r requirements.txt
python -m pytest                           # 79 tests
python -m app.cli download --timeframe 15m # also 5m, 1m, 1h
python -m app.cli backtest --preset atr_grid
python -m app.research.studies             # regenerates reports/studies/*.json (~6 min)
uvicorn app.main:app --port 8000           # API + paper loop (MOCK data by default)
cd ../frontend && npm install && npm run build   # served by the API at http://localhost:8000
#   or: npm run dev  → http://localhost:5173
```

**Broker market data:** set `MARKET_DATA_PROVIDER=BROKER`, `OANDA_API_TOKEN`, `OANDA_ACCOUNT_ID` and
`OANDA_ENVIRONMENT=practice` in the environment. Read-only either way.

## 15. Test results

`python -m pytest` in `backend/`: **79 passed** (about 40 s).

| File | Tests | Covers |
|---|---|---|
| test_basket_cycle.py | 16 | fill costs, ATR/fixed grid spacing, several adds in one move, max positions, LINEAR lots, target close and reset, loss limit (continuous and gap), no reopen after a win, longer cooldown after a loss, no-signal, volatility filter, signal-confirmed stop, pyramid, basket-drawdown stop, recovery analysis is informational |
| test_safety_and_risk.py | 21 | live trading rejected, live adapter refuses, MULTIPLIER gated and flagged, hard position ceiling, mandatory loss limit, strategy never imports broker/executor, broker has no order methods, exposure, margin, daily loss, emergency stop (positions kept, orders cancelled, resume), manual close, account halt until resume, halt during emergency stop, stale data, PAPER ACCOUNT label, env mapping, `.env` loader, secret redaction in logs, time helpers |
| test_execution.py | 6 | spread multiplier and override, slippage, commission, round-trip cost, delay fills at the later price, latency gives no systematic gain |
| test_backtest.py | 9 | no look-ahead in features, provider refuses future bars, accounting consistency, no trades in warm-up, no overlapping baskets, report contents, intrabar path order, intrabar sensitivity, determinism |
| test_market_data.py | 10 | mock candles on timeframe boundaries, Yahoo drops the forming candle, OANDA quotes and complete-only candles, token never in errors or repr, credentials from env, OHLC validation, regimes, entry rules, paper state = backtest state, warm-up never trades |
| test_paper.py | 3 | paper loop trades and persists to SQLite, stale data blocks and recovers, provider errors are throttled and never fatal |
| test_stress_walkforward.py | 7 | critical failure shows the loss, execution stress costs more, parameter-lab cap, chronological folds, lab split, windows never trade outside their dates, finer-candle intrabar path |
| test_api.py | 7 | dashboard panels, read endpoints, emergency stop / resume, restart validation, backtest job, lab guard, WebSocket push |

The frontend type-checks and builds (`tsc -b && vite build`). It was checked in headless Chromium:
- the live page with an open basket;
- a backtest run;
- the phone layout;
- STOP ROBOT → STOPPED → RESUME.
