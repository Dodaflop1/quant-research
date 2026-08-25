# Findings log

Detailed results with their reasoning, in the order they were established.
`HANDOFF.md` summarises these; this file is the working record.

---

## 2026-08-23 — Kalshi structural arbitrage is fee-bounded

### The fee ceiling

Kalshi's taker fee is

```
fee_dollars = ceil( multiplier * 0.07 * contracts * P * (1 - P) )   [to whole cents]
```

Two properties, both easy to miss:

1. **Quadratic in price**, peaking at 50¢ — not flat basis points. A flat-fee
   assumption manufactures edge near the tails.
2. **Rounds up to a whole cent, per leg.** For one contract the fee is
   `ceil(7 * P(1-P))` cents, which is **at least 1¢ at any price**.

An N-leg basket is N separate orders against a fixed 100¢ payout, so it owes at
least N cents:

| Legs | Min fee | Bid sum needed for a short |
|---|---|---|
| 2 | 2¢ | > 102¢ |
| 10 | 10¢ | > 110¢ |
| 50 | 50¢ | > 150¢ |
| 184 | 184¢ | impossible |

Bid sums cluster near 100¢ by construction. **Past ~10 legs the strategy is ruled
out by arithmetic**, before any pricing question is asked. Large fields are
therefore excluded at selection.

Computed in exact decimal arithmetic. A float implementation gave
`taker_fee(20¢, 10000) = 11201` against `taker_fee(80¢, 10000) = 11200` for the
same `P(1-P)`, because `0.2 * 0.8 = 0.16000000000000003`.

### The direction asymmetry

- **Long the basket** needs *collective exhaustiveness*. The exchange does not
  certify it — `mutually_exclusive` asserts at most one leg pays, not at least
  one. "Who will the next Pope be?" is exclusive over seven names and an
  unlisted candidate can win. Titles cannot settle it: "2027 Pro Football
  Champion" lists all 32 teams and is exhaustive; "Who will win the next
  presidential election?" lists 30 names and is not. Both read the same to a
  regex. Prices cannot settle it either — a low ask sum is *either* an arbitrage
  *or* evidence the family leaks.
- **Short the basket** needs only *mutual exclusivity*, which the exchange does
  flag. At most 100¢ is ever owed; if probability escapes to an unlisted
  outcome, every short expires worthless.

**Only the short direction is identifiable from available metadata.** All
detection prioritises it.

### Live observations

- Every family sampled had **ask sum 100.4–109.2¢** — the long direction is
  closed at the touch, which is what a market with functioning makers looks like.
- **Large-field overround is structural**: the minimum tick props up every
  longshot, giving 243¢ across 293 families in one run. Not bad data — and an
  early heuristic rejecting it as implausible was discarding the most
  interesting part of the sample.
- **Capacity is not volume.** One family: 368,438 traded volume, **one contract**
  resting at the ask. Selection filters on the thinnest leg's resting size.
- On 2026-08-25, 13 families showed bid sums above 100¢ and **none were
  tradeable** — every one had a positive gap after fees, the closest 1.0¢ short.
  Capacities across those 13: `1, 5, 94, 1, 6, 1, 0, 11, 0, 5, 2, 1, 0`.

---

## 2026-08-24 — Estimator validation, before any real data

### Study 1: recovery and CI coverage

Truth μ=0.5, α=0.8, β=1.6 (n=0.5), T=2000, ~2000 events, 40 replications.

| param | truth | mean est | bias | mean SE | emp SD | 95% coverage |
|---|---|---|---|---|---|---|
| μ | 0.5000 | 0.4921 | −1.6% | 0.0259 | 0.0205 | 95.0% |
| α | 0.8000 | 0.7800 | −2.5% | 0.0622 | 0.0685 | **85.0%** |
| β | 1.6000 | 1.5435 | −3.5% | 0.1356 | 0.1375 | 90.0% |

Coverage is the column that matters — point estimates can look fine while the
standard errors are fiction. **α coverage at 85% means intervals on the
excitation amplitude are narrower than they should be.**

### Study 2: negative control

Homogeneous Poisson, true n=0. 38 converged fits: mean 0.064, **median 0.033,
95th percentile 0.192**.

**0.19 is the operational noise floor.** Anything below it is not evidence.

### Study 3: seasonality contamination — the constraining result

Inhomogeneous Poisson, `λ(t) = r(1 + 0.8 sin(2πt/P))`. **Zero self-excitation.**

- Fitted branching ratio **mean 0.791, max 0.812** (and 0.89–0.95 at other
  parameter settings)
- Residual diagnostics rejected the fit in only **12%** of cases

A constant-baseline Hawkes has no other way to explain clustering, so it absorbs
the varying rate as excitation. The number it invents sits inside the 0.8–0.9
range published for equity order flow, and the standard goodness-of-fit
machinery misses it seven times out of eight.

### Study 4: the correction, and its specificity

Seasonal time change: estimate a periodic profile nonparametrically, transform
times through `τ(t) = ∫₀ᵗ s(u)du` so the baseline is constant by construction.
**The branching ratio is invariant under the change** — it counts offspring, not
clock time — so corrected `n` stays comparable to published figures. `β` is not
invariant and any half-life must state its clock.

```
spurious n removed:   0.891 -> 0.020   (truth 0)
genuine n preserved:  0.484 -> 0.481   (truth 0.5)
cost on real excitation: 0.003
```

Shrinking everything would be easy. **Being specific to seasonality is what
licenses quoting the corrected number.**

### The bug this caught

Unbounded log-parameters let L-BFGS-B reach where `exp()` overflows. The
objective returned a flat penalty, the gradient was exactly zero, and the
optimiser **reported success** at α ≈ 1e190.

Invisible in a single 20,000-event fit, which converged cleanly and recovered
all three parameters to within 1.5 SE. It appeared only as infinite variance
across replications at 2,000 events. **This is the argument for replication
studies in one sentence.**

Boundary solutions are now reported as non-convergence with standard errors
suppressed.

---

## 2026-08-24/25 — Data coverage and panel composition

Audit of 39.3 hours of laptop collection: 312,386 snapshots, 298 tickers.

- 2 outages totalling 15.8 min, uptime 99.33%
- Per-market sampling **median 60s, p95 60s, p99 127s, max 129s**
- Every per-market gap explained by a collector outage — no ticker silently
  stopped responding

**But zero of 298 tickers spanned the full window.** 150 entered late, 99
stopped early, 49 covered only a middle slice. Cause: the selector re-ranked by
live volume on every process start, so each of three restarts swapped part of
the panel. The log reported "0 errors" throughout and was telling the truth —
it answers whether an exception fired, not whether the panel was stable.

Separately, the volume-selected universe was **38/150 already-settled
contracts**, with only about a third surviving a six-week window: Kalshi's
volume is concentrated in same-day sports, so "busiest" and "longest-lived"
point in opposite directions.

Both fixed — `--tickers-file` pins the panel, `--min-days-to-close` filters on
the family's **soonest-closing leg** (a basket needs every leg live at once, so
its usable life is the minimum, not the maximum).

Server collection after the fix: **150/150 markets full-span, zero outages,
median and p95 60s.**

### Two bugs in the audit tool itself

- Sampling interval was measured across *all* markets, reporting "median 1s" for
  one-minute data — 150 markets are written across each ~34s cycle. Now measured
  per market.
- Per-market gaps were matched to outages by exact second, so a market last
  polled 30s before an outage looked unexplained. Now matched by overlap.

---

## 2026-08-25 — Trade density: the two projects want opposite data

| | Slow arm (pinned panel) | Fast arm (exchange-wide) |
|---|---|---|
| Median market | 162 trades / 90 days | — |
| Busiest market | 16,759 = **8/hour** | — |
| Aggregate | 1,701 trades/day | **387,000 trades/hour** |
| Markets ≥ 2,000 trades | 13 of 150 | — |
| Markets with zero trades in 90 days | 2 | — |

A factor of ~**48,000**. The top 14 markets hold 70% of the slow arm's volume.

At 8 trades/hour, arrivals are 7.5 minutes apart; any excitation detectable at
that spacing operates over **hours**, and calling it microstructural reflexivity
would be a category error. The dense tape cannot support a study of long-dated
contracts because those markets are not where the volume is.

**Two arms, one estimator, contrasted — that is Project 2's thesis.**

Also: **taker imbalance 63% buy / 37% sell** (95,538 vs 55,939), YES terms,
where a NO-side taker counts as a YES sell. Consistent with the documented
preference for buying YES over selling it in prediction markets.

### Scale correction

`--days 90` across all markets measured at ~2.8 GB/day — **~250 GB**, not the
"afternoon" originally estimated. Pagination was correct throughout (zero
duplicate ids, no repeated pages, every timestamp inside the window); only the
scope was wrong. Restricted to the pinned universe the same job took **3.3
minutes and 407 requests**.

---

## 2026-08-25 — First real fits, and what they reject

### A third to two thirds of "trades" are not arrivals

Exact-tie fractions across 13 markets: **13.6% – 70.1%, median 35.2%**. In every
market `<1ms` equals the exact-tie fraction — *identical* timestamps, several
fills reported from one aggressive order.

**Fitting raw prints is degenerate.** First attempt pinned the β upper bound in
every window of every market, β to 3.8e6/s, implied half-life ~1e-7 s.
Reproduced against known truth (n=0.500, β=1.6):

| | n | β | half-life |
|---|---|---|---|
| Raw prints | 0.664 | 198,000 | 3.5 µs |
| Aggregated orders | 0.492 | 1.696 | 0.409 s |

The raw fit **reports convergence** and returns a plausible branching ratio
inflated by a third. That is the dangerous failure, and it is exactly the regime
published order-flow estimates occupy.

Merging is anchored, not chained — each bucket fixed to its opening time — so a
dense burst cannot collapse into one arrival however long it runs.

### The fits, 9 markets, slow arm

- **9 of 9 beat Poisson on held-out likelihood.** Clustering is real.
- **Only 5 of 18 windows pass both diagnostics.**
- Branching ratios are **bimodal**: 4 markets at n≈0.88 (half-lives
  975–6,221 s), 5 at n≈0.23 (11–1,849 s), nothing between.
- Deseasonalising moved the median per-market by **+0.004**.

Bimodal `n` tracking half-life is the fingerprint of an exponential kernel
fitting long-memory data: a single exponential matches the head or the tail, not
both, so the optimiser picks a local optimum by whichever timescale dominates.

**See `docs/power_law_spec.md`.** This is the frontier.

### A reporting bug worth remembering

The fit summary printed "shrinkage +0.145", computed as
median(naive) − median(deseasonalised) across markets — a difference of medians
from *different* markets. The real median per-market shrink was **+0.004**, a
factor of 35, in the direction that flattered the correction.

**Never quote a statistic you have not defined.**
