# SPARKZ Adaptive Grid & Basket: technical report

*Study run 30 September 2026. Every figure below comes from
`reports/study_20260930.json`, produced by `python -m app.research.study`,
and can be regenerated. All results are simulated. None of them is a forecast.*

## 1. Verdict

**No robust statistical edge was found.** On the primary market (XAUUSD, 15-minute),
the default ATR grid lost money after costs and hit its 10% account-drawdown halt. Over
the longer 1-hour history (2.4 years) it lost 22% with the halt switched off, and 59 of 60
randomly perturbed variants also lost.

The strategy has the classic grid profile. It wins about two baskets in three, but each
losing basket costs about twice what a winning one earns. That needs a 67% win rate just
to break even; it achieved 64–66%.

Every grid variant (A–E) was compared with a **control that uses the same entries, targets
and stops but never adds a second position**. The control beat the default ATR grid in 9 of
10 market and timeframe samples, and beat every grid variant in 5. On gold 1h it made
+$2,164 while every grid lost. Where the entry signal had value, the grid mostly gave it back.

The few positive results, such as pyramiding on 15m gold or some target and spacing
settings, come from short samples, flip sign when neighbouring parameters change, and
did not carry into walk-forward test windows. They are not evidence of an edge.

This system should not trade real money in its current form.

## 2. How the strategy works

One basket at a time, in a fixed cycle:

1. **Analyse the closed bar** (`strategy/market_analysis.py`, `regime_detector.py`).
   - Indicators: EMA 20/50/200, RSI 14, ATR 14, MACD 12/26/9, Bollinger 20/2, ADX 14 with ±DI,
     realized volatility, 20-bar return, candle body and wick shares, and distance from each
     EMA in ATRs.
   - Trend regime:
     - TRENDING_UP when ADX ≥ 20, EMA20 > EMA50, close > EMA50 and +DI > −DI;
     - TRENDING_DOWN is the mirror image;
     - RANGING when ADX ≤ 18;
     - UNCERTAIN otherwise.
   - Volatility regime: ATR% in its own trailing 500-bar window; HIGH at or above the 85th
     percentile, LOW at or below the 15th.
   - All values use bars up to and including the current one only. A test recomputes each
     row on truncated data and checks it is identical.
2. **Entry** (`entry_engine.py`).
   - BUY when EMA20 > EMA50, close > EMA20, RSI > 50 and ADX ≥ 20. SELL is the mirror image.
   - Never in WARMUP or UNCERTAIN bars, and (by default) never in HIGH_VOLATILITY.
   - The signal forms at bar N's close. The order fills at bar N+1's open, or later if an
     entry latency is configured, and pays half the spread plus slippage.
3. **Grid** (`grid_engine.py`). The next level is measured from the previous entry's trigger price:
   - A: fixed price step;
   - B: ATR × 0.5, using the ATR of the last closed bar;
   - C: the same ATR step, but only while EMA20 vs EMA50 still backs the basket's direction.
   Averaging grids add when price moves against the basket; pyramiding (D) adds only when
   it moves in favour. At most one entry per bar by default.
4. **Sizing** (`position_sizing.py`).
   - FIXED 1,1,1,1,1 × base lot; LINEAR 1,2,3,4,5; PYRAMID 1,1,1,1,1 on favourable adds;
     MARTINGALE 1,2,4,8,16 (refused unless `allow_martingale`).
   - Base lot: 0.01, or with `base_lot_mode: ATR_NORMALIZED`, set once per basket so a
     1-ATR move is worth `usd_per_atr`.
5. **Basket** (`basket_manager.py`).
   - P&L = Σ direction × (exit price − fill price) × lots × contract size, minus commissions.
     Exit prices are executable (bid for BUY, ask for SELL).
   - Target, default: basket P&L ≥ 0.5% of equity at the basket's start.
   - Loss limit: 1% of that equity; the tighter of `max_basket_loss_percent` and `risk_per_cycle`.
6. **Risk engine** (`risk/risk_manager.py`). It checks every position before the fill, and a
   refusal always wins. In order:
   - halted;
   - position limit (5, hard ceiling 20);
   - daily loss (3%);
   - cooldown (8 bars after a losing basket);
   - exposure (10× equity notional);
   - margin (50%).
   An account drawdown of 10% from peak closes the basket and halts trading until a person resumes it.
7. **Reset.** After a close, a new basket needs a fresh signal from a later bar. Winning
   never triggers a re-entry and losing never enlarges the next basket; both are tested.

**Inside a bar**, adds, the target, the loss limit and the account halt fire in the order
price reaches them. When a bar's high and low could be read either way, the engine runs
both orders and keeps the one with less equity at the close (PESSIMISTIC). With the default
settings this ambiguity arose in only 4 of 2,535 open-basket bars on 15m gold. The
alternative OHLC-path assumption gave an identical 15m result, and 1h results within $200.

## 3. Data and assumptions

| Market | Source | 15m history | 1h history |
|---|---|---|---|
| XAUUSD | COMEX gold futures GC=F (spot proxy) | 22 Jul – 30 Sep 2026 (4,545 bars) | May 2024 – Sep 2026 (13,729) |
| EURUSD, GBPUSD, USDJPY | Yahoo FX | 8 Jul – 30 Sep 2026 | Dec 2023 – Sep 2026 |
| BTCUSD | Yahoo BTC-USD | 2 Aug – 30 Sep 2026 | Oct 2024 – Sep 2026 |

**Costs per fill.** Half the spread plus slippage:

| Market | Spread | Slippage |
|---|---|---|
| Gold | 0.30 | 0.05 |
| EURUSD | 1.2 pips | 0.2 pips |
| GBPUSD | 1.5 pips | 0.3 pips |
| USDJPY | 1.5 pips | 0.3 pips |
| BTCUSD | $20 | $10 |

No commission by default. **Margin:** 1:100 for FX and gold, 1:2 for BTC.

**Main limitations:**
- Yahoo serves only about 60 days of 15m bars, so the primary sample is short: 116–125 baskets.
- Gold data is futures, not spot.
- Spreads are constant; broker CSV imports can carry per-bar spreads.
- Overnight swap/financing is **not** modelled. The average basket lasts about 5.5 hours
  on 15m and about 12 hours on 1h, so many cross a rollover.

## 4. Results

### 4.1 XAUUSD 15m: the spec's configuration (fixed 0.01 lot, $10,000)

| Strategy | Baskets | Win rate | Net P&L | Profit factor | Max DD | Largest loss | Avg win | Baskets at 5 positions | Max leverage | Halted |
|---|---|---|---|---|---|---|---|---|---|---|
| Control: single position | 27 | 81.5% | +$720 | 2.65 | −2.8% | −$108 | $52 | — | 0.4× | no |
| A: Fixed grid (step 4.12) | 125 | 68.0% | +$166 | 1.04 | −6.5% | −$107 | $51 | 54 | 2.3× | no |
| B: ATR grid | 116 | 65.5% | −$219 | 0.95 | −10.0% | −$108 | $51 | 52 | 2.4× | **yes** |
| C: Signal-confirmed | 118 | 66.9% | −$32 | 0.99 | −8.3% | −$107 | $51 | 46 | 2.3× | no |
| D: Pyramiding | 102 | 69.6% | +$383 | 1.12 | −5.1% | −$106 | $51 | 33 | 2.3× | no |
| E: Martingale (HIGH RISK) | 92 | 60.9% | −$753 | 0.79 | −10.0% | −$102 | $50 | 0 | 7.5× | **yes** |

The martingale grid never reached 5 positions: the 5th entry (0.16 lot, 31 oz in total,
about $130k notional) broke the 10× exposure limit and was refused every time.

### 4.2 XAUUSD 15m deep dive (strategy B)

- **Configured run:** 116 baskets, −$219, halted on 25 Sep 2026 at the 10% account drawdown.
- **Halt switched off:** 124 baskets and −$183 (−1.8%).
  - Win rate 66.1% against a break-even of 67.0%. Average win $50.6, average loss −$102.9.
  - Max drawdown −11.1%. Longest losing streak 4. Sharpe −0.35.
- **Positions per basket:** 1 → 19 baskets, 2 → 17, 3 → 10, 4 → 24, 5 → 54.
  The 54 full baskets won only 29.6% of the time and lost $3,092 between them.
- **By regime:** baskets opened in TRENDING_UP made +$50 (61 baskets); TRENDING_DOWN lost
  −$225 (63). Low volatility made +$411 (8 baskets, all winners); normal volatility lost −$586.

### 4.3 XAUUSD 1h (strategy B, 2.4 years)

- **Configured run:** halted on 29 Jul 2024, after 28 baskets and −$885.
- **Halt off:** 572 baskets and −$2,224 (−22.2%).
  - Win rate 64.0% against a break-even of 67.0%. Profit factor 0.88.
  - Max drawdown −24.5%. Longest losing streak 7.
  - The 113 baskets that reached 5 positions won 28.3% and lost $5,903.
- **By regime:** sells (TRENDING_DOWN entries) lost −$2,413; buys made +$189. Gold rose
  strongly over 2024–26, so this reflects the period, not a skill.
- **Sizing variants (fixed 0.01 lot, halt off):**
  - FIXED −$2,224, LINEAR −$2,194, MARTINGALE −$2,510.
  - PYRAMID +$456, profit factor 1.03, max DD −10.9%.
- **Control (max positions 1):** +$1,510, profit factor 1.26, 189 baskets, max DD −5.8%.

### 4.4 Other markets (ATR-normalised sizing: 1 ATR = $10 on the base lot)

A fixed 0.01 lot is about $8 per 15m ATR on gold but $0.36 on EURUSD. At that size the $50
target was unreachable: the EURUSD control made one basket in three months. So every market
is compared at equal risk per ATR, with strategy A's step set to half that market's median ATR.

| Market | Control | Best grid variant | Other grid variants (A, B, C, E, and D unless it's the best) |
|---|---|---|---|
| XAUUSD 15m | +$401 | D +$644 (PF 1.19) | A +$529, C +$375, B +$218, E −$451 |
| XAUUSD 1h | **+$2,164** (PF 1.39) | — | all −$885 to −$999, all halted |
| EURUSD 15m | −$477 | — | all ≈ −$1,000, all halted |
| EURUSD 1h | −$888 | — | −$672 to −$857, all halted |
| GBPUSD 15m | +$336 | — | −$913 to −$1,023, all halted |
| GBPUSD 1h | +$424 | — | −$841 to −$973, all halted |
| USDJPY 15m | +$45 | — | ≈ −$900 to −$955, all halted |
| USDJPY 1h | −$378 | — | −$338 to −$997, all halted |
| BTCUSD 15m | −$302 | E −$112 | −$414 to −$723 |
| BTCUSD 1h | −$224 | D +$132 (PF 1.01) | −$264 to −$581, mostly halted |

On FX, ATR-normalised baskets are large relative to equity, so the 10× exposure limit bound
and refused many adds (EURUSD 15m grids averaged about 2.5 positions). The risk engine was
doing its job. **Across the 50 grid-variant runs, 45 lost money and 42 hit the 10% halt.**

## 5. Robustness

| Test (strategy B, halt off) | XAUUSD 15m | XAUUSD 1h |
|---|---|---|
| Basket order shuffled: max DD, median (5–95%) | −8.7% (−12.3 to −6.0) | −27.9% (−34.6 to −23.8) |
| Baskets resampled: return, median (5–95%) | −1.3% (−15.4 to +11.1) | −22.8% (−46.8 to +1.9) |
| Chance of a loss / of hitting −10% | 56% / 41% | 93% / 99% |
| 60 re-simulations with random costs, latency and parameters: median return | +2.1% (72% profitable) | −22.2% (2% profitable) |
| Walk-forward: in-sample vs out-of-sample average return | +8.4% vs −0.04% (1 of 4 steps profitable) | +16.3% vs +1.4% (3 of 4 profitable, falling to −1.3% in the last) |
| Period by period | 3 of 9 weeks profitable | 12 of 29 months profitable |

The 15m sample is too short to tell luck from edge: the bootstrap's 90% range spans
−15% to +11%. On 1h, where the sample is 4.6 times larger, the answer is clearly
negative. Walk-forward shows the classic overfitting gap. Parameters chosen as best on
past data earned +8% to +24% in-sample and about 0% out-of-sample.

### Sensitivity (strategy B, XAUUSD, halt off, net P&L)

| Parameter | 15m | 1h |
|---|---|---|
| Grid spacing ATR × 0.15 / 0.5 / 1.0 / 1.5 / 2.0 | +337 / −183 / +488 / +1,367 / +761 | −1,905 / −2,224 / −1,193 / −1,009 / −378 |
| Sizing FIXED / LINEAR / PYRAMID / MARTINGALE | −183 / −1,390 / +383 / −901 | −2,224 / −2,194 / +456 / −2,510 |
| Max positions 1 / 2 / 3 / 5 / 8 | +720 / +82 / +487 / −183 / +222 | **+1,510** / +947 / −1,006 / −2,224 / −1,021 |
| Target 0.1% / 0.25% / 0.5% / 1% / 2% | −509 / +307 / −183 / +1,459 / +792 | −1,771 / −2,006 / −2,224 / −844 / +655 |
| Spread × 0 / 1 / 3 | +17 / −183 / −457 | −1,978 / −2,224 / −2,901 |

The 15m results swing by $1,500 between neighbouring settings with no consistent shape,
which is the sign of noise, not structure. On 1h every spacing loses. More positions per
basket is consistently worse, and the loss is there **even at zero spread**.

### ML (P(profitable basket | conditions at the signal bar), XAUUSD 1h)

572 baskets were split chronologically: 343 train, 114 validation, 115 test, with a purge
between splits.

| Model | Test AUC | Brier score (base rate 0.2269) | Baskets it kept | Their test P&L |
|---|---|---|---|---|
| Logistic regression | 0.56 | 0.2268 | 21 | −$8 |
| Random forest | 0.49 | 0.2331 | 16 | −$201 |

For comparison, keeping every test basket gave −$229. Neither model predicts meaningfully
better than the base rate. The logistic "improvement" rests on 21 baskets and is noise.

## 6. The fourteen questions

1. **How does it enter?** Trend rules on the closed bar (EMA20/50, price vs EMA20, RSI,
   ADX ≥ 20). It does not trade in uncertain, warm-up or high-volatility bars, and fills
   at the next bar's open.
2. **When does it add?** When price moves the grid distance against the basket from the
   previous entry. That distance is 4.12 on A, and ATR × 0.5 on B and C, with C also
   requiring the trend to still agree. Pyramiding adds on favourable moves instead. At
   most one add per bar; every add must pass the risk engine.
3. **How is basket profit determined?** From the combined P&L of all positions at the
   executable exit price, after commissions, against a target of 0.5% of starting equity
   by default. Fixed USD, risk/reward and ATR targets are also available.
4. **When does it close?** At the target, at the 1% basket loss limit, at the optional
   time stop, at the 10% account-drawdown halt, or at the end of data.
5. **How much exposure?**
   - A full FIXED basket on gold is 0.05 lot: 5 oz, about $21k notional, 2.1× leverage on
     $10k, and $41 per 1-ATR move on 15m.
   - Martingale reached 7.5× leverage and would have exceeded the 10× cap on its 5th entry.
   - Margin use at 1:100 stayed under 2.5%, except for the martingale at 7.5%.
6. **What happens in a strong one-way move?**
   - The trend entry mostly rides it. Synthetic strong trends earned +$4,000 to +$23,000
     at 86–99% win rates.
   - A 25-ATR slide laid over real 15m data didn't hurt: +$906, max DD −7%.
   - A one-way move is **not** this strategy's main failure mode.
7. **What happens in high volatility?** New baskets are skipped by default, so exposure
   is limited to baskets already open. A 5× range blow-out with a −10 ATR shock left 15m
   results about unchanged (−$34 vs −$183).
8. **How often does it lose?** About 34–36% of baskets, with streaks up to 4 (15m) or 7 (1h).
   The **sideways, mean-reverting market is the failure mode**:
   - 15m: 51.5% win rate, −$2,159, −24.5% drawdown;
   - 1h: −$2,814, −30.3% drawdown.
9. **How large are the losing baskets?** About −$88 to −$103 on average, against about
   $43–51 for winners. The largest loss is 2.1–2.9 average wins. 20–22% of winning baskets
   were at some point down more than they finally earned, and none by more than 3×. So
   profits are small relative to the open risk carried, but the basket loss limit keeps
   that risk bounded.
10. **Maximum drawdown?**
    - 15m: −11.1% with the halt off; the configured account halted at −10%.
    - 1h: −24.5% with the halt off; it halted after 2.5 months.
    - A worst-case ordering of the 15m baskets gives −43%.
11. **Sensitive to grid spacing?** Very, and not in a consistent direction on 15m. On 1h
    every spacing loses; wider spacing loses less.
12. **Sensitive to position sizing?** Yes. LINEAR and MARTINGALE both make results worse
    and drawdowns deeper. Pyramiding was the only sizing mode with positive results on
    both gold timeframes, and only slightly positive.
13. **Viable after spread and slippage?** On 15m gold the default grid is roughly
    break-even before costs (+$17 at zero spread) and negative after. On 1h it loses even
    with zero spread.
14. **Stable across periods?** No. 3 of 9 weeks were profitable on 15m and 12 of 29
    months on 1h, and walk-forward out-of-sample returns were near zero.

## 7. Strengths, weaknesses, failure modes

**Strengths**
- Risk stays bounded. The loss limit caps every basket at about 1% of equity, plus gap
  slippage (worst: −$126 on $10k). Max positions, exposure and margin limits can't be
  exceeded, and the account halt fails closed.
- The trend-following entry avoids the textbook grid disaster of averaging into a
  crash: strong one-way moves were profitable.
- A high win rate (64–70%), which is what makes grids attractive.

**Weaknesses**
- The per-basket payoff is inverted: the average loss is about 2× the average win.
  That means the win rate must reach about 67% just to break even.
- Adding to losers concentrates risk in the baskets most likely to fail. Full baskets won
  only 28–30% of the time.
- Performance depends on sample and parameters. Nothing carried out-of-sample.

**Failure modes, most to least damaging**
1. Choppy sideways markets: trend signals flip, grids fill, and baskets stop out.
2. Martingale sizing: the same losses at up to 16× size. The exposure limit is the only
   thing that stopped it here.
3. Wider spreads, and on 15m any cost at all.
4. Parameter drift: the best past setting didn't transfer.

## 8. Codebase audit

**Found and fixed during the study:**
- **Fixed lot across markets** made the FX targets unreachable. Added ATR-normalised sizing
  and used it for cross-market comparisons.
- **The account halt masked the stress tests.** Scenarios injected after the halt looked
  identical to the baseline. Diagnostics now run with the halt off and flag
  `would_breach_account_limit`.
- **The loss-limit sensitivity sweep was inert above 1%**, because the limit is the tighter
  of two settings. Both now move together, and a test asserts it.
- **Settings copied without validation** (`model_copy`) produced enum warnings. They now
  use enums, and every worker re-validates, so martingale permission and live-trading
  refusal can't be bypassed.
- **Engine speed:** pandas row access and deep copies were replaced; the engine is 13×
  faster with identical results.
- **Duplicated code:** basket-row persistence and the robustness and walk-forward bundles
  were duplicated between the API, CLI and paper trader. Each now has one implementation.
- **Test harness mistakes:** three test expectations were corrected. Each time the engine
  was right and the expectation wrong: gap fills on adds, a permutation bound, and a random
  walk counted as "sideways".

**Verified (with tests):**

| Check | What the tests show |
|---|---|
| No look-ahead | Features match on truncated data. Fills happen at the next bar's open. Grid distances use the previous bar's ATR. Past decisions don't change when future bars are removed. |
| No ML leakage | Splits are chronological with a purge. Features come from the bar before each basket opened. The threshold is chosen on validation. |
| P&L correct | Hand-computed basket P&L, commissions, USDJPY conversion, and the inverse target and stop prices. |
| Sizing correct | Each mode's sizes, lot rounding, and a loss never enlarging the next basket. |
| No unlimited grid | Parametrised for FIXED, LINEAR and MARTINGALE over a 300-bar one-way move, with several adds allowed per bar and every other limit disabled: never more than max positions. |
| Leverage visible | Notional, effective leverage and margin are reported for every run. |
| Risk controls work | Tests cover each of the position limit, exposure, margin, daily loss, cooldown and account halt. |
| Paper matches backtest | Stepping in chunks gives exactly the same baskets and P&L as a single backtest. |

**Security**
- No API keys or secrets anywhere.
- Live trading is refused at configuration load (the API won't start), and the broker
  seam only offers a simulated broker.
- CORS allows only localhost.
- The API has **no authentication**, so it must stay on localhost.
- The ORM is used throughout, so there's no SQL built from strings.

**Imports:** pyflakes is clean. 108 tests pass.

**Known approximations (not bugs)**
- USDJPY P&L is converted at each bar's open.
- MFE can show more than the target when a bar opens beyond it, because the close is
  conservatively booked at the target level.
- Gaps fill at the worse price for stops and adds.
- No swap or financing costs.
- The base lot doesn't grow with equity.
- One basket per account at a time.

## 9. Before any live use

None of these conditions is met today:

1. At least 2 years of **spot** 15m data with real per-bar spreads (MT5 export via
   `import-csv`), including swap costs.
2. A configuration that is profitable out-of-sample in walk-forward on that data, is
   stable under the perturbation test (at least 80% of variants profitable), and beats
   the single-position control.
3. At least 3 months of paper trading (`paper-create` / `paper-step`) whose results fall
   inside the backtest's range.
4. A broker adapter with authentication, idempotent orders and reconciliation. None
   exists in version 1, by design.

The most promising direction in this data is **not** the grid: it's the entry signal on
its own (the single-position control on gold 1h: +$1,510 to +$2,164, profit factor
1.26–1.39, max DD under 6%). That deserves its own out-of-sample test before anything else.
