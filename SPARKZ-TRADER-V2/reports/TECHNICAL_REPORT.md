# SPARKZ TRADER V2: Technical Report

*Market data + adaptive grid & basket simulator. Report date: 2026-10-01; §9.6 (long 1m study) and §16
(structure scalping and a setup-filter model) added 2026-10-02.*

Everything below is **simulation**: historical replays and synthetic scenarios, with simulated execution against a
PAPER ACCOUNT. No real order was ever placed, and nothing here can place one.

Results are stated as "configuration X produced Y over period Z under assumptions A". They are not forecasts.
Historical results, paper results, live market conditions and real execution are four different things.

Study outputs: `reports/studies/*.json`, produced by `python -m app.research.studies`, plus the long 1m study
(`fine_path_dukascopy_1m*.json`, `python -m app.research.fine_path_study`, §9.6) and the structure-scalping
studies (`scalp_structure_dukascopy.json`, `setup_model_dukascopy_*.json`, §16).

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
7. **Long real-1m check (§9.6):** 2.7 years of Dukascopy XAUUSD 1m data (Jan 2024 → Oct 2026, gold +106%).
   - On the plain 15m OHLC path, adverse-first still shows up to **+$36,372** (`multiplier_high_risk`). With the
     real 1m candles as the intrabar path, the same run **loses $1,984**.
   - At the backtest's 0.30 spread, three presets stay positive on the 1m path, all thin: `atr_grid` up to
     +$3,805 (profit factor 1.16), `video_style` +$2,927 (1.07, adverse-first only), `fixed_grid` up to +$1,429.
   - Dukascopy's **measured spread is 0.54 median** (p90 0.79, p99 1.36), not 0.30. At 0.54, **every preset
     loses on the 1m path in every ordering** (−$906 to −$1,984), and 16 of 18 runs hit the 20% account
     drawdown halt.
8. **Structure scalping (§16):** a market-structure module (swings, BOS / CHoCH, Asia range, liquidity sweeps,
   no look-ahead) drives a 1m/5m scalping setup with a 15m bias, tested on the same 2.7 years at 0.54 spread.
   - All **36 parameter combinations lose**, in-sample (2024) and out-of-sample (2025-01 → 2026-10).
   - Before costs the setups break even (−0.09R to +0.05R per trade); spread and slippage cost 0.07–0.24R.
   - On 15m and 1h setups (§16.4) costs drop to 0.02–0.07R, but results scatter around zero and none is
     statistically distinguishable from chance (best t = 0.64).
   - A walk-forward **setup-filter model** (gradient boosting, retrained monthly on earlier trades only) cuts the
     1m setup's loss from −0.109R to −0.049R per trade, still negative. It mainly learned to avoid setups whose
     stop is small against the spread; a plain "bigger stops" rule did as well, and the model's rankings do not
     order real results.
9. Building V2 exposed **four ways a backtest can flatter a grid**. Each is fixed and covered by tests:
   - intrabar order (§9.2);
   - latency modelled by path interpolation (§9.4);
   - a risk refusal that skipped the loss-limit check (§7);
   - a thesis check too slow to ever trigger (§4).

**Conclusion:** on the data available, no grid configuration here shows evidence of a robust edge.
- The positive in-sample numbers depend on the intrabar-order assumption, low costs, or both.
- The longest out-of-sample test (1h, 2.3 years) is negative in every fold.
- The longest real-path test (1m, 2.7 years) is negative for every preset at the measured spread.
- Structure scalping is negative in every tested form, with or without a learned filter (§16).
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
  were available at the time; §9.6 repeats the check on 2.7 years of real 1m data.

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

### 9.6 Long real-1m check (Dukascopy, 2.7 years) and the measured spread

**Data.** Dukascopy's free bid/ask feed (`download --source dukascopy --start 2024-01-01`), stored apart from
Yahoo's GC=F future. 977,051 one-minute mid candles, 2024-01-01 → 2026-10-01, no invalid rows; the 15m
decision candles (65,155) are resampled from them. The only missing weekdays are the three Good Fridays.
Gold rose **+106%** over the period ($2,035 → $4,182). OANDA was the planned source but could not be used (its
service is not offered in the requester's country). Summary: `reports/studies/dukascopy_download_summary.json`.

**Measured spread** (ask − bid at each 1m close, all 977k minutes):

| | Median | p90 | p99 | Backtest default |
|---|---|---|---|---|
| Dukascopy XAUUSD | **0.54** | 0.79 | 1.36 | 0.30 |

A bank feed's spread is usually tighter than a retail broker's, so 0.54 is itself likely optimistic.

**Method.** `python -m app.research.fine_path_study --source dukascopy --path 1m [--spread 0.54]`: every preset ×
every intrabar order on 15m decisions, with the plain 15m OHLC path and with the real 1m candles as the
intrabar path. Trading 2024-01-10 → 2026-10-01 (after 600 bars of warm-up), $10k start.

**Net P&L** (⛔ = stopped by the 20% account-drawdown halt; Fav. / Rand. / Adv. = intrabar order):

| Preset | 15m path @0.30 (Fav. / Rand. / Adv.) | 1m path @0.30 | 1m path @0.54 |
|---|---|---|---|
| baseline | −141 / −341 / −731 | −291 / −291 / −311 | −1,010 / −1,030 / −1,030 |
| fixed_grid | −579⛔ / +8,103 / +15,308 | −582⛔ / +201 / +1,429 | −928⛔ / −928⛔ / −906⛔ |
| atr_grid | −417⛔ / +10,653 / +18,107 | −432⛔ / +1,146 / +3,805 | −1,081⛔ / −1,078⛔ / −1,083⛔ |
| signal_grid | −1,472⛔ / +1,325 / +1,676 | −1,318⛔ / −1,321⛔ / −1,321⛔ | −1,250⛔ / −1,250⛔ / −1,249⛔ |
| video_style | −1,776⛔ / +2,118⛔ / +35,887 | −1,969⛔ / −1,152⛔ / +2,927 | −1,942⛔ / −1,934⛔ / −1,686⛔ |
| multiplier_high_risk | −1,869⛔ / −1,987⛔ / +36,372 | −1,976⛔ / −1,977⛔ / −1,984⛔ | −1,968⛔ / −1,972⛔ / −1,964⛔ |

**1m path @0.54, adverse-first** (the ordering that flatters grids most on the 15m path):

| Preset | Baskets | Win rate | Avg win | Avg loss | Largest basket loss | Max DD | Profit factor |
|---|---|---|---|---|---|---|---|
| baseline | 2,351 | 47.6% | $10.1 | −$10.0 | −$16 | −12.3% | 0.92 |
| fixed_grid | 1,109 | 95.0% | $10.0 | −$205 | −$227 | −20.0% ⛔ | 0.92 |
| atr_grid | 1,389 | 94.9% | $10.1 | −$202 | −$300 | −20.1% ⛔ | 0.92 |
| signal_grid | 1,261 | 94.7% | $10.1 | −$198 | −$325 | −20.1% ⛔ | 0.91 |
| video_style | 311 | 92.3% | $10.0 | −$190 | −$206 | −19.2% ⛔ | 0.63 |
| multiplier_high_risk | 205 | 89.8% | $10.0 | −$181 | −$201 | −20.0% ⛔ | 0.48 |

What it shows:
- **The 15m-path profits are an artefact of the intrabar assumption.** Random and adverse-first gains of
  +$8k to +$36k on the 15m path shrink to +$0.2k to +$3.8k, or become losses, once the real minutes are used.
- **Costs decide the rest.** At 0.30 the three surviving grids earn profit factors of 1.01–1.16. The extra
  0.24 per fill at 0.54 (baseline: $712 → $1,270 of spread over about 4,700 fills) turns all of them negative.
  With an average win near $10 and an average loss near $200, a grid needs about 95% wins just to break even;
  the cost increase pushes the win rate below that.
- **Why ordering still matters on the 1m path at 0.30** (`atr_grid` −$432 / +$1,146 / +$3,805). Basket by
  basket, the three runs are identical for most of the period (the first 54 baskets match exactly; about 1,800
  baskets open at the same times until February 2026). The gap has two sources:
  - **Same-minute round trips.** The ordering still applies inside each 1m candle. In fast minutes (several
    moved $14–15; the median 1m range is $1.0, p99 $8.3) adverse-first lets the grid add at the minute's low
    and reach the target at its high. Baskets whose target filled in the same minute as their last add:
    2 (favourable-first), 30 (random), 52 (adverse-first). Eight of them turned a loss-limit close (about
    −$210) into a +$10 target (about +$1,400 in total).
  - **The 20% account-drawdown halt.** Favourable-first crossed it on 2026-02-24 and stopped trading; random
    came within 1.4 points (worst −18.6%) and adverse-first reached −13.2%. After that date the two survivors
    made +$1,295 and +$2,809 that the halted run could not.
  Neither is a code defect: the 1m path still does not resolve these grids in fast minutes, so adverse-first
  and random are optimistic there, and **favourable-first is the figure to use on the 1m path**. A run that
  ends within a couple of points of the halt is close to a coin toss.
- **The baseline** (single position, $10 target and stop) never halts; it loses about $1,000 at 0.54 against
  about $300 at 0.30, i.e. roughly the extra spread it pays.

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
  - Yahoo: 15m covers about 2 months; 5m about 5 weeks; 1m only a few days (plus the daily archive).
  - Dukascopy (§9.6): 2.7 years of real 1m bid/ask, spot XAUUSD. It is one bank feed: its spread (0.54 median)
    is likely tighter than a retail broker's; an MT5 export (`import-csv --source mt5`) shows a given broker's.
  - Backtests still use **one constant spread** per run (0.30 default, `--spread` / `execution.spread_override`
    to change it). Real spreads widen at session opens, rollover and news (p99 1.36).
  - The OANDA provider is implemented and tested against a fake client, but **was not run against a real
    account** (the token available was invalid, and OANDA is not offered in the requester's country).
- **Intrabar path.** The order of highs and lows is unknown, and grid results depend on it heavily (§9.2–9.3).
  Real 1m candles narrow this a lot (§9.6), but the ordering still moves the ATR and fixed grids on the 1m
  path at 0.30 spread; tick data, or a check of what else the ordering setting affects, would settle it.
- **Execution.** No partial fills, requotes, stop-outs, weekend swap or financing. A gap is filled at the next
  price with normal spread; real spreads widen at gaps and news.
- **Latency model** (backtest) is a statistical approximation (§9.4).
- **Sample size.** The 15m windows give 7–11-day walk-forward folds. The 1h run is longer, but the presets were
  designed for 15m.
- **Paper persistence.** A restart begins a new paper account. Earlier sessions stay in SQLite but are not resumed.
- **ML.** The only model is the research setup filter (§16.3); the trading engine has no ML component.
  scikit-learn is a dependency for that study only.
- **Scalping study.** One instrument, one data feed, a constant spread, fills at the stop level plus fixed
  slippage; the stop is assumed whenever stop and target share a 1m candle. Real scalping fills are worse in
  fast markets.
- **"VIDEO_STYLE_MODE"** is an approximation from a description, not the video's code.

## 14. Setup instructions

See `README.md` for full details. In short:

```bash
cd SPARKZ-TRADER-V2
cp .env.example .env                       # never commit .env; real env variables take precedence
cd backend && pip install -r requirements.txt
python -m pytest                           # 115 tests
python -m app.cli download --timeframe 15m # also 5m, 1m, 1h
python -m app.cli backtest --preset atr_grid
python -m app.research.studies             # regenerates reports/studies/*.json (~6 min)
python -m app.cli download --source dukascopy --start 2024-01-01   # 2.7 years of 1m + 15m (rate-limited: hours)
python -m app.research.fine_path_study --source dukascopy --path 1m [--spread 0.54]   # §9.6 (~75 min)
python -m app.research.scalp_study --source dukascopy --split 2025-01-01               # §16.2 (~3 min)
python -m app.research.scalp_study --tfs 15m,1h --min-trades 50                        # §16.4 (~2 min)
python -m app.research.setup_model --tf 1m --swing-n 3 --variant choch --rr 2.0         # §16.3 (~2 min)
uvicorn app.main:app --port 8000           # API + paper loop (MOCK data by default)
cd ../frontend && npm install && npm run build   # served by the API at http://localhost:8000
#   or: npm run dev  → http://localhost:5173
```

**Broker market data:** set `MARKET_DATA_PROVIDER=BROKER`, `OANDA_API_TOKEN`, `OANDA_ACCOUNT_ID` and
`OANDA_ENVIRONMENT=practice` in the environment. Read-only either way.

## 15. Test results

`python -m pytest` in `backend/`: **115 passed** (about 55 s).

| File | Tests | Covers |
|---|---|---|
| test_basket_cycle.py | 16 | fill costs, ATR/fixed grid spacing, several adds in one move, max positions, LINEAR lots, target close and reset, loss limit (continuous and gap), no reopen after a win, longer cooldown after a loss, no-signal, volatility filter, signal-confirmed stop, pyramid, basket-drawdown stop, recovery analysis is informational |
| test_safety_and_risk.py | 21 | live trading rejected, live adapter refuses, MULTIPLIER gated and flagged, hard position ceiling, mandatory loss limit, strategy never imports broker/executor, broker has no order methods, exposure, margin, daily loss, emergency stop (positions kept, orders cancelled, resume), manual close, account halt until resume, halt during emergency stop, stale data, PAPER ACCOUNT label, env mapping, `.env` loader, secret redaction in logs, time helpers |
| test_execution.py | 6 | spread multiplier and override, slippage, commission, round-trip cost, delay fills at the later price, latency gives no systematic gain |
| test_backtest.py | 9 | no look-ahead in features, provider refuses future bars, accounting consistency, no trades in warm-up, no overlapping baskets, report contents, intrabar path order, intrabar sensitivity, determinism |
| test_market_data.py | 10 | mock candles on timeframe boundaries, Yahoo drops the forming candle, OANDA quotes and complete-only candles, token never in errors or repr, credentials from env, OHLC validation, regimes, entry rules, paper state = backtest state, warm-up never trades |
| test_paper.py | 3 | paper loop trades and persists to SQLite, stale data blocks and recovers, provider errors are throttled and never fatal |
| test_stress_walkforward.py | 7 | critical failure shows the loss, execution stress costs more, parameter-lab cap, chronological folds, lab split, windows never trade outside their dates, finer-candle intrabar path |
| test_oanda_history.py | 4 | OANDA history paging without gaps or duplicates, end date, separate store per source, start required |
| test_archive.py | 4 | daily write-once archive of 1m/5m candles |
| test_mt5_import.py | 3 | MT5 export into its own store, server time → UTC, spread in price units, point size, missing-store hint |
| test_dukascopy.py | 10 | bi5 decoding, mid + spread, flats dropped, 0-based month in URL, HTTP errors reported, 429 retried and day files cached, mis-scaled prices rejected, 1m + 15m stores, website CSV exports (GMT and local time), 1-minute check |
| test_structure_scalp.py | 12 | pivots, swings known only after confirmation, BOS/CHoCH, no look-ahead under truncation, sweeps, Asia range known after 07:00, 15m bias from closed candles only, trade costs exact, stop assumed when stop and target share a candle, short with a gap through the stop, too-small risk skipped, session filter |
| test_setup_model.py | 3 | model features unchanged when later candles are removed (1m and 5m setups), walk-forward trains only on trades exited before the test month |
| test_api.py | 7 | dashboard panels, read endpoints, emergency stop / resume, restart validation, backtest job, lab guard, WebSocket push |

The frontend type-checks and builds (`tsc -b && vite build`). It was checked in headless Chromium:
- the live page with an open basket;
- a backtest run;
- the phone layout;
- STOP ROBOT → STOPPED → RESUME.

## 16. Market structure, a scalping setup and a setup-filter model

*Research code in `app/research/`; results in `reports/studies/scalp_structure_dukascopy.json` and
`setup_model_dukascopy_{1m,5m}.json`. Data: Dukascopy XAUUSD 1m (§9.6). Spread 0.54, slippage 0.05 per fill,
$100 risked per trade.*

### 16.1 Market structure (`structure.py`)

- **Swing high / low:** a pivot that is the extreme of `n` candles on each side. It is used only from the
  candle that confirms it (`n` candles later), never earlier.
- **Trend, BOS, CHoCH:** the trend changes only on a candle CLOSE beyond the last confirmed swing. A close beyond
  it in the trend's direction is a break of structure (BOS); the first close against the trend is a change of
  character (CHoCH) and flips the trend.
- **Sessions and liquidity:** Asia 00–07, London 07–12, New York 12–21 UTC; the day's Asia range is known from
  07:00. A liquidity sweep is a wick through the last swing or the Asia high/low with the close back inside.
- Tests: truncating the series leaves every earlier row unchanged; swings appear only after confirmation;
  BOS/CHoCH on a hand-built series.

### 16.2 The scalping setup (`scalp.py`)

Long (short mirrored): last closed 15m candle bullish; a CHoCH up on the 1m or 5m setup chart (a pullback
turning back up), optionally after a liquidity sweep of a low; signal between 07:00 and 20:00 UTC. Entry at the
next 1m open at the ask; stop below the lowest low of the last 30 setup candles (minus 0.20); target at
1, 1.5 or 2 × risk; time stop after 120 minutes; one trade at a time. Exits are checked on every 1m candle at
the bid; when the stop and target share a candle the stop is assumed.

**Study (`scalp_study.py`):** 36 combinations (setup chart 1m/5m × swing size 2/3/5 × with/without sweep ×
target 1/1.5/2R), chosen on 2024 and judged on 2025-01 → 2026-10.

| Setup chart | Trades (in / out of sample) | Net R per trade, in-sample | Net R per trade, out-of-sample | Gross R (before costs), OOS | Cost per trade | Average stop |
|---|---|---|---|---|---|---|
| 1m | 1,361–2,832 / 2,208–5,002 | −0.205 to −0.241 | −0.101 to −0.150 | −0.020 to +0.016 | 0.11–0.24R | $3.4–7.2 |
| 5m | 266–607 / 238–693 | −0.147 to −0.192 | −0.020 to −0.107 | −0.029 to +0.054 | 0.07–0.12R | $6.4–10.2 |

- **All 36 combinations lose** in both periods (out-of-sample −$0.9k to −$73k at $100 per trade).
- The in-sample choice (5m chart, swing size 2, CHoCH, 2R) made −0.064R per trade out of sample over 690 trades
  (−$4,431, win rate 40.7%, profit factor 0.86); 2025 −0.101R, 2026 +0.045R.
- **The signals carry about no edge before costs**, and the costs are a large share of a scalp's risk: on 1m
  charts 11–24% of the stop on every trade. Larger setups (5m) lose less because their stops are larger.

### 16.3 A setup-filter model (`setup_model.py`)

- **Features** (21, known when the setup appears): direction, hour, weekday, session; stop size against ATR and
  against the spread; size of the structure break; body of the signal candle; pullback depth; recent sweep;
  distance to the Asia high/low; 15- and 60-minute momentum; RSI; age and slope of the 15m trend; distance to
  the 15m swing; the day's range so far.
- **Label:** each setup's net R, simulated on its own (overlapping trades allowed), after spread and slippage.
- **Model:** `HistGradientBoostingRegressor` (depth 3, learning rate 0.03, 200 iterations, at least 200 trades
  per leaf), deliberately small for a noisy target.
- **Walk-forward:** from 2025-01, each month is predicted by a model trained on all earlier trades whose exit
  happened before the month began (a purge; tested). A setup is traded when its predicted R is above 0; the
  chosen setups are then traded one at a time, like the unfiltered setup, over the same months.

| Setup | Labelled trades | Unfiltered (OOS) | Filtered, predicted R > 0 | > 0.05 | > 0.10 |
|---|---|---|---|---|---|
| 1m, swing 3, CHoCH, 2R | 8,802 | 3,500 trades, −0.109R, −$38,205 | 566, −0.049R, −$2,790 | 289, −0.088R | 131, −0.022R |
| 5m, swing 2, CHoCH, 2R | 1,404 | 690 trades, −0.064R, −$4,431 | 90, −0.109R, −$980 | 39, +0.008R | 16, +0.211R |

What it shows:
- **No filtered version is reliably profitable.** The only positive figures come from 16–39 trades.
- **The model does not rank setups by outcome.** Mean out-of-sample R by prediction quintile (lowest to highest):
  1m −0.177 / −0.079 / −0.165 / −0.120 / −0.074; 5m −0.017 / −0.045 / −0.008 / −0.125 / −0.118 (backwards).
  The 1m rank correlation (0.20) is misleading: a losing trade's R is −1 minus its cost share, which the model
  can predict from the stop size without predicting direction.
- **A trivial rule matches it.** Taking the same number of 1m setups with the largest stops (in spreads) gave
  +0.029R against the model's −0.049R. That rule picks its cut-off from the whole test period, so it is not a
  tradable result, but it shows the model's gain is mostly cost avoidance. Both lose in 2025.

### 16.4 The same setup on 15m and 1h

`python -m app.research.scalp_study --tfs 15m,1h --min-trades 50` (`scalp_structure_dukascopy_15m-1h.json`).
Each setup timeframe gets its own settings: 15m setups use a 1h bias, a 20-candle stop window, an 8-hour
maximum hold and stops up to $40; 1h setups use a 4h bias, a 12-candle window, a 2-day hold and stops up to
$80. Targets 1.5, 2 or 3R; same costs; chosen on 2024, judged on 2025-01 → 2026-10.

| Setup chart | Trades (in / out of sample) | Net R per trade, in-sample | Net R per trade, out-of-sample | OOS combinations > 0 | Cost per trade |
|---|---|---|---|---|---|
| 15m | 102–240 / 126–351 | −0.110 to −0.287 (all 18 negative) | −0.047 to +0.065 | 8 of 18 | about 0.04–0.07R |
| 1h | 28–66 / 22–87 | −0.156 to +0.064 | −0.130 to +0.075 | 9 of 18 | about 0.02R |

- **Costs stop being the problem:** about 0.02R per trade on 1h setups (average stop $37), against 0.11–0.24R
  on 1m scalps.
- **But there is no edge to keep.** Before costs, the setups are still around zero. Every 15m combination
  lost in 2024; the out-of-sample figures scatter around zero, and settings that won in-sample lost
  out-of-sample (1h, swing 3, CHoCH, 3R: +0.064R → −0.045R).
- **Nothing is statistically distinguishable from chance.** The in-sample choice (1h, swing 2, sweep + CHoCH,
  3R) made +0.075R per trade out of sample (70 trades, +$526, profit factor 1.13), with a standard error of
  0.178R (t = 0.42). It lost in 2025 (−0.034R) and won in 2026 (+0.191R); its longs lost and its shorts won,
  in a market that rose 106%. The best 15m figure (+0.065R, 126 trades) has t = 0.64. A t-statistic near 2
  would be needed before calling any of these an edge.
- **Too little data at these timeframes.** 2.7 years give only 22–87 one-hour trades out of sample. Judging
  them would need more history (Dukascopy's gold feed goes back many years; a longer download is rate-limited
  and would take most of a day).

**Conclusion:** on 2.7 years of real 1m gold data at the measured spread, the structure setups have no edge
that survives costs: scalps (1m/5m) lose clearly, with or without a learned filter, and the 15m/1h versions are
indistinguishable from zero. Larger timeframes remove the cost problem but leave too few trades to show an
edge in this period.
