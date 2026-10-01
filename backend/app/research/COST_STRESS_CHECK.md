# Cost stress test: does the hourly edge survive higher trading costs?

*Run 1 October 2026. The figures come from `cost_stress.json`, produced by
`python -m app.research.cost_stress`.*

## The test

The 7 hourly paper accounts passed the random-entry check (`RANDOM_ENTRY_CHECK.md`). Much of
their return comes in the first hour or two after a signal, so trading costs eat into it
directly.

Each account's targets backtest was re-run with spread and slippage multiplied by 1×, 1.5×,
2×, 3×, 4× and 6×. Everything else stayed the same: the same data window as the reset targets,
the same rules, stops and sizing. Costs are charged on both entry and exit.

**Pass line, fixed before running.** At **2× the modeled costs**, an account needs all three of:
- a net return above 0;
- a profit factor of at least 1.05;
- at least 60% of its calendar quarters profitable, counting only quarters with 10 or more trades.

## Results

| Account | Round-trip cost at 1× | 1× | 1.5× | 2× | 3× | Break-even | PF at 1× → 2× | Profitable quarters at 2× | Result |
|---|---|---|---|---|---|---|---|---|---|
| gold_hourly_long | 3.0 bp | +100% | +65% | +53% | +22% | **4.0×** (11.7 bp) | 1.26 → 1.16 | 8/10 | PASS |
| gold_hourly_both | 3.0 bp | +224% | +131% | +103% | +36% | **3.4×** (10.1 bp) | 1.29 → 1.18 | 8/10 | PASS |
| btc_hourly_both | 5.7 bp | +211% | +129% | +72% | −2% | 3.0× (16.8 bp) | 1.17 → 1.07 | 6/8 | PASS |
| eth_hourly_both | 11.8 bp | +276% | +132% | +68% | −17% | 2.8× (32.9 bp) | 1.16 → 1.06 | 6/8 | PASS |
| btc_hourly_long | 5.7 bp | +72% | +48% | +30% | −27% | 2.5× (14.3 bp) | 1.13 → 1.06 | 5/8 | PASS (narrowly) |
| eth_hourly_long | 11.8 bp | +85% | +44% | +20% | −23% | 2.5× (29.0 bp) | 1.15 → 1.04 | 4/8 | **FAIL** |
| usdjpy_hourly_long | 2.2 bp | +61% | +14% | −2% | −40% | **1.95×** (4.3 bp) | 1.15 → 1.00 | 5/11 | **FAIL** |

How to read the table:
- **Break-even** is the cost multiple at which the backtest's net return reaches zero. In
  brackets is the round-trip cost in basis points at that multiple.
- **Returns compound, so they shrink faster than profit factor.** A profit factor of about 1.07
  over a thousand trades still adds up to a large return. Results below about −45% hit the
  backtest's 50% drawdown halt.

**5 of the 7 hourly accounts pass. eth_hourly_long and usdjpy_hourly_long fail.**

## What it means against real costs

The modeled costs are spread plus slippage only. **No exchange or broker commission is
included.** Whether an account's edge is real in practice depends on how its break-even cost
compares with what the account would actually pay.

The figures below are rough reference levels only. Check your own broker.

- **Gold (break-even about 10–12 bp per round trip).** Retail gold spreads are often around
  0.2–0.4 USD, about 0.5–1 bp per round trip at today's price. That leaves a large margin, and
  gold is the most cost-robust market here.
- **BTC (break-even about 14–17 bp).** A spot exchange charging 0.1% taker fees on each side
  costs about 20 bp per round trip in fees alone, which is **above break-even**. The BTC
  accounts' edge only holds with low fees: maker orders, a fee tier around 0.02–0.05% per side,
  or a broker whose spread is the only cost.
- **ETH (break-even about 29–33 bp).** At 0.1% taker fees each side, the total of about 20 bp
  fees plus the modeled 12 bp sits right at break-even. That's marginal.
- **USD/JPY (break-even about 4.3 bp).** A tight retail spread of about 1 pip (about 0.6 bp)
  plus slippage fits, but there's little room. Commission-based FX accounts often add about
  0.5–1 bp, which leaves almost no margin.

## Timing

Even at 1× costs, the edge is uneven across quarters, and several accounts' most recent
quarters are weaker:
- btc_hourly_long lost money in 2026 Q2.
- eth_hourly_both lost money in 2026 Q3.
- gold_hourly_long lost money in 2026 Q2.
- usdjpy_hourly_long lost money in 2026 Q2 and Q3. At 2× costs it has lost money for the last
  three full quarters.

This doesn't mean the edge is gone. But the paper results over the next weeks will come from
conditions closer to these recent quarters than to the strong 2025 ones.

## Conclusion

- **Gold hourly** (both accounts): robust. It survives 3× costs and its break-even sits well
  above realistic spreads.
- **BTC and ETH, buy-and-sell accounts**: pass, but only hold up with low fees. The modeled
  costs leave out exchange commissions, which would remove most of the BTC edge at 0.1% taker
  fees.
- **BTC long-only**: passes narrowly. **ETH long-only**: fails at 2×.
- **USD/JPY hourly**: fails. Its edge disappears below 2× costs, and it has weakened recently.

The paper accounts use the same modeled costs as the backtest, so they won't show this
problem themselves. The paper results only carry over to live trading at a broker whose costs
are close to the 1× level in the "Round-trip cost at 1×" column.
