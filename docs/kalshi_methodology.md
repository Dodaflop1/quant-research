# Kalshi mispricing engine — methodology

*Last updated 2026-08-25. Sections are written as results arrive. Anything not
yet done says so rather than being left blank, because a reader should be able
to tell the difference between "no edge here" and "not looked at yet".*

**The headline result so far is negative, and it is the most useful thing in
this document.** Structural arbitrage on Kalshi event contracts is bounded not
by how mispriced the market gets but by the fee schedule's rounding rule, which
imposes a floor that scales with the number of legs. Past roughly ten legs, no
dislocation of any size can clear it. That is an arithmetic result, not an
empirical one, and it holds regardless of how the data comes out.

---

## 1. Objective and scope

Detect and quantify structural mispricing in Kalshi event contracts —
mispricing identifiable **without a forecasting model**, from internal
consistency conditions that must hold whatever the true probabilities are.

- **Universe.** 150 markets across 63 mutually exclusive families, pinned
  2026-08-25 from 12,093 open events. Selected for multi-leg structure,
  tradeable depth on the *worst* leg, and a settlement date beyond the study
  window. Composition is US elections, Fed decisions, division winners, and one
  macro index comparison.
- **Period.** Order books from 2026-08-25 00:53 UTC, ongoing. Trade prints
  backfilled to 2026-05-28.
- **Resolution.** 60-second full-depth order book snapshots; trade prints at
  their native timestamps.
- **Status.** No capital deployed. Nothing here is a live or paper trading
  result. Everything is measurement of the opportunity set.

## 2. Data

### 2.1 Sources

| Data | Endpoint | Cadence |
|---|---|---|
| Order books | `GET /markets/{ticker}/orderbook` | 60s, full depth |
| Event catalogue | `GET /events?with_nested_markets` | hourly, written only on change |
| Trade prints | `GET /markets/trades`, `GET /historical/trades` | backfilled |

Raw JSON responses are appended verbatim to daily JSONL with their receive time
*before* any parsing. Order books cannot be re-fetched, so a parser bug costs a
reparse rather than a day of data. All parsing happens offline.

### 2.2 Coverage, measured rather than assumed

A collector reporting "0 errors" is stating that it never raised an exception.
It is not stating that sampling was regular or that the panel was stable. Those
are different claims, and `scripts/audit_coverage.py` checks them against the
raw files.

Server collection, first 47.7 minutes:

- **150 of 150 markets span the full window**, zero partial
- **Zero outages**
- Per-market sampling: **median 60s, p95 60s, p99 127s, max 129s**

The p99 tail is one skipped cycle for about 1% of intervals, caused by the trade
backfill contending for the same account rate limit during that window. It is
explained, not unexplained.

Quote the **median as the sampling rate and the p95 as the tail**. Do not quote
a figure computed across markets: 150 markets are written across the ~34 seconds
of each cycle, so the gap between consecutive *writes* is about one second while
any individual market waits a minute. An earlier version of the audit reported
exactly that and would have claimed one-second resolution for one-minute data.

### 2.3 A prior panel that must not be concatenated

An earlier 39.3-hour collection (2026-08-23 05:17 → 2026-08-24 21:01, 312,386
snapshots) used a **different, volume-selected universe** and is not part of this
series. Its audit is the reason the current design exists:

- 298 distinct tickers, and **zero spanned the full window**
- 150 entered late, 99 stopped early, 49 covered only a middle slice

Cause: the selector re-ranked by live volume on every process start, so each
restart swapped part of the panel. Uptime was 99.33% and the log reported no
errors throughout. Both were true. Neither was the relevant question.

The fix is to pin the universe to an explicit ticker list, with the volume and
liquidity filters *disabled* when pinned — re-applying them would drop pinned
markets whose volume had since fallen, which is the same churn by another route.

### 2.4 Selection is not neutral, and neither is the obvious alternative

Ranking candidate families by volume fills the universe with same-day sports,
because that is where Kalshi's volume is. The 2026-08-24 universe, selected that
way, was **38/150 already-settled contracts**, with only about a third surviving
a six-week window.

Families are therefore filtered on time-to-expiry, applied to the family's
**soonest-closing leg**. A basket needs every leg live simultaneously, so its
usable lifetime is the minimum across legs, not the maximum — taking the maximum
lets one same-day leg disguise itself as a month-long series.

This is a real trade-off, stated plainly: the resulting universe is thinner. It
is the only universe that can produce a multi-week series at all.

### 2.5 Reconstructed rather than observed

The API returns **bid ladders on both sides**. A resting NO bid at *q* is
economically a YES ask at *100 − q*, so the book is stored once in YES terms and
NO-side views are derived. **This complementarity has not yet been verified
against live payloads.** It is an assumption about Kalshi's book representation
and it is load-bearing for every price in this document.

### 2.6 Survivorship

The panel is pinned and long-dated, so within the study window essentially
nothing settles — which means the sample does **not** yet contain settled
outcomes. That is a limitation for calibration work (§4) and a non-issue for
structural arbitrage, which is a statement about simultaneous prices rather than
about outcomes.

---

## 3. Structural arbitrage (model-free)

### 3.1 The bucket-sum condition

For a mutually exclusive, collectively exhaustive family, exactly one leg pays
100¢. So:

- **Long the basket** — buy one of each leg for `ask_sum`, receive 100¢.
  Profitable if `ask_sum + fees < 100`.
- **Short the basket** — sell one of each leg for `bid_sum`, pay 100¢.
  Profitable if `bid_sum − fees > 100`.

### 3.2 The two directions are not symmetric, and only one is identifiable

This is the most important structural point in the strategy.

**Long requires collective exhaustiveness.** The exchange does not certify it.
`mutually_exclusive` asserts that *at most* one leg resolves YES — not that at
least one does. "Who will the next Pope be?" is exclusive over seven listed
candidates and an unlisted candidate can win. Titles cannot settle it either:
"2027 Pro Football Champion" lists all 32 teams and is exhaustive; "Who will win
the next presidential election?" lists 30 names and is not. Both read identically
to a regex.

Nor can prices settle it. An ask sum below 100¢ is **either** an arbitrage
**or** evidence that the family leaks probability to an unlisted outcome, and a
snapshot cannot distinguish them.

**Short requires only mutual exclusivity**, which the exchange *does* flag. At
most one leg pays, so at most 100¢ is ever owed. If probability escapes to an
unlisted outcome, every short expires worthless and the premium is kept.

So the short direction is the one the available metadata can identify. All
detection work prioritises it accordingly.

### 3.3 The fee ceiling — the binding constraint

Kalshi's taker fee is

```
fee_dollars = ceil( multiplier × 0.07 × contracts × P × (1 − P) )   [to whole cents]
```

with *P* the price in dollars. Two properties matter and both are easy to miss:

1. **It is quadratic in price**, peaking at 50¢, not flat basis points. Assuming
   flat fees manufactures edge near the tails.
2. **It rounds up to a whole cent, per leg.** For a single contract, the fee is
   `ceil(7 × P(1−P))` cents — which is **at least 1¢ for any price**, and 2¢ near
   the middle.

The second property is the ceiling. An N-leg basket is N separate orders, so it
owes **at least N cents** against a fixed 100¢ payout:

| Legs | Minimum fee | Bid sum required for a short |
|---|---|---|
| 2 | 2¢ | > 102¢ |
| 5 | 5¢ | > 105¢ |
| 10 | 10¢ | > 110¢ |
| 50 | 50¢ | > 150¢ |
| 184 | 184¢ | impossible |

Bid sums cluster near 100¢ by construction. **Past roughly ten legs, no
dislocation of any size can close the gap** — the strategy is ruled out by
arithmetic before any pricing question is asked. Large fields are therefore
excluded at selection, not filtered later.

Everything is computed in exact decimal arithmetic. A float implementation gave
`taker_fee(20¢, 10,000) = 11,201` against `taker_fee(80¢, 10,000) = 11,200` for
what is the same `P(1−P)`, because `0.2 × 0.8` evaluates to `0.16000000000000003`.

### 3.4 What the live data shows

**The long direction is closed at the touch.** Every family sampled on
2026-08-23 had an ask sum above 100¢, ranging 100.4¢ to 109.2¢. Summing asks
means crossing the spread on every leg, so this is what a market with functioning
makers looks like. Whether it opens transiently is what the time series exists to
answer.

**Large-field overround is structural, not anomalous.** The minimum tick props up
every longshot, so a many-outcome field's ask sum runs far above 100¢ by
construction — 243¢ across 293 families in one run.

**Thirteen families showed bid sums above 100¢ on 2026-08-25, and none were
tradeable.** Every one had a positive gap after fees; the closest was 1.0¢ short.
That is finding §3.3 holding on a completely different universe from the one that
produced it.

**Capacity is not volume.** One family showed 368,438 in traded volume and a
*single contract* resting at the ask. Historical activity says nothing about what
can be lifted now, which is why selection filters on the thinnest leg's resting
size rather than on volume.

Observed capacities across the 13 flagged families, in contracts on the worst
leg: `1, 5, 94, 1, 6, 1, 0, 11, 0, 5, 2, 1, 0`. One outlier and a lot of zeros.

### 3.5 Monotonicity — not yet done

Nested threshold families must have non-increasing cumulative probabilities.
Detector not yet implemented.

### 3.6 Cross-venue — not yet done, and deliberately deferred

Settlement rules differ between venues even when the question reads identically,
and that is where naive versions of this strategy lose money. This deserves more
space than the detection logic and will not be attempted until the single-venue
work is finished.

---

## 4. Fair value (model-dependent) — not yet started

Deferred deliberately. One domain finished beats three started, and the
structural work has not yet produced its backtest. When it happens, calibration
will be reported as a reliability diagram, Brier score with its
reliability/resolution decomposition, log loss, and Brier skill score against the
base rate. Accuracy alone will not be reported: a forecaster that cannot beat the
base rate has no business sizing a position however accurate it looks.

Note the survivorship constraint in §2.6 — a long-dated panel yields few settled
outcomes, so calibration needs a deliberately different sample.

## 5. Backtest — not yet started

## 6. Capacity

Partially answered by §3.4 and unusually early, because the depth data forced it.
Observed resting size on the worst leg of a flagged family is typically **single
digits**. Whatever the eventual edge, this is a strategy measured in tens of
contracts, not thousands. The full treatment awaits the backtest.

## 7. Why would this edge exist

Not yet answered with evidence, and it should not be asserted without any. The
candidate explanations for a retail-heavy venue are thin liquidity,
favourite–longshot bias, and price-insensitive hedgers. The observed data is at
least consistent with the first: spreads wide enough that crossing them exceeds
the dislocation, and depth in single contracts.

The honest current answer is that **the fee schedule is a plausible complete
explanation for why the gap persists** — there is no edge to close, so no one
closes it.

## 8. What did not work

### 8.1 An ask-sum plausibility band

Testing the ask sum against a band around 100¢ rejected 293 families quoting
243¢ as "implausible". They were not bad quotes. In a large field the minimum
tick props up every longshot, so a summed ask far above 100¢ is the overround —
a structural feature of many-outcome markets, and precisely where the short
direction is worth measuring. The heuristic was discarding the most interesting
part of the sample.

### 8.2 Ranking by total volume

Sorting candidate families by volume favoured 47- and 50-leg fields that can
never clear their own fees, and they consumed the entire market budget: one live
run selected three families, two of them structurally dead. Ranking now uses
distance to a tradeable gap. Statistical power here comes from the number of
independent families watched, not from legs within one family.

### 8.3 Ranking on the long gap

Including the long direction in the ranking promoted leaky families, because a
family that leaks probability has a low ask sum for reasons that have nothing to
do with mispricing. Ranking uses the short gap only — the identifiable direction.

### 8.4 Reading the documented field names

A parser written from the documented response fields returned **zero quoted
families across 4,987 mutually exclusive events** against production. The live
payload uses unit-suffixed names (`yes_ask_dollars`, `volume_fp`) that the
documentation does not consistently show. The failure looked exactly like "no
data" rather than "wrong parser", which is the dangerous kind.

## 9. Limitations

- **The fee model is unverified against a settled fill.** Rounding granularity
  and the per-series multiplier (documented 0–2) are taken from documentation,
  not observation. Every number in §3.3 inherits this.
- **YES/NO complementarity is unverified** (§2.5) and load-bearing for every
  price here.
- **No backtest exists**, so there is no realised edge, drawdown, or hit rate.
  Nothing in this document should be read as a return estimate.
- **Two collection panels exist** and must not be concatenated (§2.3).
- **The universe is deliberately biased** toward long-dated markets (§2.4). It is
  not representative of Kalshi, and results should not be generalised to the
  exchange.
- **No settled outcomes yet**, so nothing is calibrated.

## 10. Reproduction

```bash
# what would be collected, and why everything else was dropped
python scripts/collect_kalshi.py --discover \
    --min-days-to-close 45 --min-volume 100 --max-markets 150

# collect, pinned to a fixed panel
python scripts/collect_kalshi.py --auto --tickers-file ./data/universe.txt \
    --interval 60 --out ./data

# verify what was actually collected
python scripts/audit_coverage.py --data ./data --per-market

# trade prints for the same universe
python scripts/backfill_trades.py --days 90 --tickers-file ./data/universe.txt
```

Python 3.12. Collection runs under systemd; see `deploy/README.md`. Fee model,
selection logic and audit are covered by the unit suite (`pytest tests/unit`).
