# Entry signal validation (single position, no grid)

*Run 30 September 2026. The figures come from `reports/entry_validation_20260930.json`,
produced by `python -m app.research.entry_validation`.*

## Why

The grid study's most promising result was its control: the same trend entry
(EMA20/50, price vs EMA20, RSI, ADX ≥ 20) with one position per basket, the 0.5% target
and the 1% loss limit. On XAUUSD 1h it made **+$1,510 over 189 baskets, profit factor 1.26,
max drawdown −5.8%**. That was a single pass over one history, so it got the same scrutiny
as the grid. The pass criteria were fixed in the code before the study ran.

## Verdict: **failed**. The profit is gold's rise, not the signal's timing

| # | Criterion (all needed) | Result | |
|---|---|---|---|
| 1 | Walk-forward out-of-sample profitable, in ≥ 3 of 4 windows | −$81 in total; 2 of 4 windows profitable | ✗ |
| 2 | ≥ 80% of 60 perturbed variants profitable | 98% (median +11.0%) | ✓ |
| 3a | Beats the 95th percentile of random-direction entries at the same moments | 97th percentile | ✓ |
| 3b | Beats the 95th percentile of random-timing always-BUY entries | **48th percentile** | ✗ |
| 4 | Every calendar year profitable | 2024 (May–Dec): −$5 · 2025: +$925 · 2026: +$697 | ✗ (narrowly) |
| 5 | Profitable at 2× spread | +$1,167 | ✓ |

**The decisive test is 3b.** Gold rose 72% over the sample (about $1,750 on 1 oz). Buying on
random bars, at the same rate the rules fire (40% of eligible bars) and with the same target
and stop, earned a **median of +$1,552**, with a 5–95% range of +$864 to +$2,090. The signal's
+$1,510 sits at the 48th percentile of those random runs. The rules add nothing that random
buying doesn't.

The direction split shows the same thing:

| Direction | Baskets | Net P&L | Win rate |
|---|---|---|---|
| Buy | 108 | **+$1,516** | 75.9% |
| Sell | 81 | −$10 | 65.4% |

The signal "beat" random direction (test 3a) only because it leaned long, 108 buys to 81
sells, in a rising market. A buy-only version made +$2,215, which again is simply more
exposure to gold's rise.

**The other markets** (same rules, ATR-normalised size) agree. Where a market trended up,
the buys made money and the sells lost; elsewhere it lost:

| Market | Net P&L | Profit factor | Buys | Sells |
|---|---|---|---|---|
| GBPUSD 1h | +$424 | 1.08 | +$482 | −$101 |
| USDJPY 1h | −$378 | 0.90 | +$236 | −$655 |
| EURUSD 1h | −$445 | 0.90 | −$2 | −$443 |
| BTCUSD 1h | −$224 | 0.93 | −$139 | −$112 |
| XAUUSD 15m | +$720 | 2.65 | +$455 | +$262 |

The XAUUSD 15m row is 27 baskets, too few to judge.

## What passed, and why it doesn't rescue the result

The result is **stable** under cost and parameter changes: 98% of perturbations profitable,
and still profitable at 2× and 3× spread. That's what you'd expect from a strategy whose P&L
mostly comes from being long a rising asset. Stability of the drift doesn't mean the timing
has skill. Walk-forward, which picks parameters on past data, was about break-even
out-of-sample: +$248, −$350, −$429, +$450.

## Conclusion

Neither the grid nor the entry signal shows an edge beyond the market's own direction over
this sample. On gold 2024–26, "buy and wait for +0.5% / −1%" worked because gold went up;
the rules didn't choose better moments than chance. Paper-trading this signal would mostly
measure whether gold keeps rising.

A genuine test of timing skill needs one of these:
- a market or period without a strong trend;
- a benchmark-relative measure, P&L minus the same exposure to buy-and-hold, used as the
  target from the start;
- or different entry logic.
