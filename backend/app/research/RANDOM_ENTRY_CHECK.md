# Random-entry check: do the paper strategies time the market better than chance?

*Run 30 September 2026, 200 random runs per test. Figures come from `random_entry.json`, produced by
`python -m app.research.random_entry --runs 200`.*

## The test

Each paper account's own targets backtest was re-run with the signal column replaced. The data
window, costs, ATR stop, 2R target, sizing and risk limits all stayed the same:

- **Random timing:** the account's real signal series is circularly shifted by a random 10–90% of
  its length. The runs of consecutive signals are kept intact, so the random runs trade about as
  often, but out of step with prices.
- **Random direction:** applies only to the buy-and-sell (`baseline`) accounts. Signals fire at
  the real moments, but each run of consecutive signals gets BUY or SELL by coin flip.

**Pass line, fixed before running:** the real net return must beat the 95th percentile of every
random test that applies to the account.

## Look-ahead fixed first

Before running, I found and fixed three look-ahead problems in the original backtester
(`app/backtest/engine.py`). Each one used information from the entry bar that isn't known at its
open:

1. The stop and target were sized from the **entry bar's own ATR**, which includes that bar's
   high and low. They now use the ATR of the signal bar.
2. A new position was **never checked against the rest of its entry bar**. Stops hit in the first
   hour were only seen on the next bar. The entry bar is now checked, and when both the stop and
   the target fall inside it, the stop counts as hit first.
3. Trades were sized from **equity marked at the entry bar's close**. They are now sized from the
   previous close.

### Effect on the stored paper targets

The old engine reproduces all ten stored targets. With the fix:

| Account | Trades | Return | Profit factor | Win rate | Max drawdown |
|---|---|---|---|---|---|
| btc_daily_long | 53 → 55 | +25.1% → +22.9% | 1.79 → 1.68 | 49.1% → 47.3% | −8.0% → −8.1% |
| btc_hourly_both | 891 → 1000 | +250.9% → +228.0% | 1.22 → 1.18 | 39.3% → 38.7% | −24.0% → −22.8% |
| btc_hourly_long | 501 → 552 | +102.7% → +76.6% | 1.19 → 1.14 | 39.3% → 38.2% | −27.6% → −30.0% |
| eth_daily_long | 52 → 62 | +9.3% → +7.0% | 1.30 → 1.19 | 40.4% → 38.7% | −8.5% → −10.6% |
| eth_hourly_both | 906 → 980 | +234.0% → +300.9% | 1.17 → 1.18 | 39.3% → 39.8% | −24.9% → −24.9% |
| eth_hourly_long | 538 → 580 | +81.6% → +82.9% | 1.16 → 1.15 | 38.7% → 38.6% | −21.9% → −24.1% |
| gold_daily_long | 46 → 54 | +32.5% → +33.4% | 2.29 → 2.07 | 54.3% → 51.9% | −4.7% → −4.7% |
| gold_hourly_both | 626 → 677 | +148.2% → +225.0% | 1.25 → 1.30 | 39.3% → 40.3% | −18.8% → −19.7% |
| gold_hourly_long | 398 → 430 | +91.8% → +100.3% | 1.27 → 1.26 | 40.0% → 40.0% | −22.1% → −25.6% |
| usdjpy_hourly_long | 465 → 491 | +72.5% → +62.6% | 1.17 → 1.15 | 38.9% → 38.5% | −18.9% → −19.0% |

Trade counts rise because positions stopped out in their first bar now free their slot sooner.
Profit factors mostly fall slightly.

**The stored targets were not changed.** They were fixed on purpose so results can't be judged
against moved goalposts. Whether to reset them with `--set-targets` is the account owner's call.

## Results (fixed engine)

| Account | Real return | Trades | Buy & hold | Random-timing median / 95th pct | Real's percentile | Random-direction median / 95th pct | Real's percentile | Result |
|---|---|---|---|---|---|---|---|---|
| btc_daily_long | +22.9% | 55 | +45.5% | +5.8% / +25.3% | 93rd | — | — | **FAIL** |
| eth_daily_long | +7.0% | 62 | −25.4% | −2.1% / +13.8% | 86th | — | — | **FAIL** |
| gold_daily_long | +33.4% | 54 | +136.1% | +19.7% / +39.4% | 87th | — | — | **FAIL** |
| btc_hourly_both | +228.0% | 1000 | +32.9% | −33.9% / +46.2% | 100th | −37.3% / +59.9% | 100th | PASS |
| btc_hourly_long | +76.6% | 552 | +32.9% | −31.3% / +16.1% | 100th | — | — | PASS |
| eth_hourly_both | +300.9% | 980 | +3.5% | −42.0% / +19.7% | 100th | −39.0% / +34.0% | 100th | PASS |
| eth_hourly_long | +82.9% | 580 | +3.5% | −34.4% / +18.5% | 100th | — | — | PASS |
| gold_hourly_both | +225.0% | 677 | +81.4% | −43.4% / +18.9% | 100th | −45.8% / −10.5% | 100th | PASS |
| gold_hourly_long | +100.3% | 430 | +81.4% | −9.3% / +26.4% | 100th | — | — | PASS |
| usdjpy_hourly_long | +62.6% | 491 | +10.5% | −44.4% / −29.1% | 100th | — | — | PASS |

**All 7 hourly accounts pass.** Each beat every one of the 200 random runs. **All 3 daily accounts
fail.** Their results sit at the 86th–93rd percentile of random timing: better than typical, but
not clearly better than luck, and with about 55 trades each there's too little data to tell. Gold
daily also made a quarter of what buying and holding gold made.

## Checks on the hourly result

A 100th-percentile result is strong enough to suspect remaining look-ahead, so two more checks were
run:

- **Delayed entry.** If the edge came from a bar-alignment artifact, entering a few bars later
  should wipe it out. If it comes from following trends, it should fade gradually.

  | Account | Real | +1 bar | +2 bars | +3 bars | +6 bars |
  |---|---|---|---|---|---|
  | btc_hourly_both | +228% | +242% | +95% | +118% | +106% |
  | btc_hourly_long | +77% | +50% | +41% | 0% | +8% |
  | eth_hourly_both | +301% | +110% | +92% | +64% | +51% |
  | eth_hourly_long | +83% | +35% | +2% | +2% | −20% |
  | gold_hourly_both | +225% | +186% | +114% | +9% | +73% |
  | gold_hourly_long | +100% | +116% | +81% | +49% | +90% |
  | usdjpy_hourly_long | +63% | +31% | +35% | +19% | −24% |

  Returns fall gradually as entries get later. Even 6 hours late, most accounts stay well above
  the random-timing median of −30% to −45%. That fits an edge from being on the right side of an
  established trend, not an artifact.
- **Entry price.** Filling at the signal bar's close instead of the next bar's open changes
  results only slightly (for example, btc_hourly_both goes from +228% to +261%). The next open is
  typically within 0.2–2.2 bp of the previous close.

The signal rules themselves only use the closed bar: EMA20 against EMA50, RSI and close.

## What this does and doesn't show

- The hourly strategies' backtest profits are **not** what random entries with the same exits
  would have made. The random runs lose 30–45% on costs and stop-outs, while the real signals
  make money in both directions, including on ETH, which went nowhere (+3.5%) over the window.
- It's still **one history** of about two years (2024–26), with costs taken from
  `app/markets/instruments.py`. The edge fades quickly with delay: much of the return comes from
  the first 1–2 hours after a signal. So in live trading, execution speed and real spreads matter
  more than the backtest suggests.
- The daily accounts give no evidence of skill so far. Paper-trading them mainly tracks the
  market's direction.
