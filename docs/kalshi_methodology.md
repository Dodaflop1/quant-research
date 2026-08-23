# Kalshi mispricing engine — methodology

> Skeleton. Each section states what has to go in it and the question a reader
> will ask. Sections are written as results arrive, not at the end.

## 1. Objective and scope

What is being claimed, and over what universe and period. Name the contracts,
the date range, and the data resolution. State explicitly whether results are
paper or live, and at what size.

## 2. Data

- Source and endpoint for each field; collection cadence.
- Gaps: what is missing, over what periods, and how gaps are handled.
- Anything reconstructed rather than observed, and the assumption behind it —
  including the YES/NO complementarity used to store one side of the book.
- Survivorship: are settled and delisted contracts in the sample, or only the
  ones that still exist?

## 3. Structural arbitrage (model-free)

### 3.1 Bucket sum

Exhaustive, mutually exclusive buckets must have YES prices summing to 100¢
before friction. State the friction bound: fees both ways, spread crossed on
each leg, and the worst-case fill. A violation is only tradeable if it clears
that bound, not if it merely exists.

### 3.2 Monotonicity

Nested thresholds must be ordered. Give the specific market families checked
and the rule applied to each.

### 3.3 Cross-venue

Settlement rules differ between venues even when the question looks identical.
Document the differences for every pair traded — this is where naive versions
of this strategy lose money, so it deserves more space than the detection logic.

### 3.4 Results

Opportunities per day, size available at detection, realised versus theoretical
edge, and the distribution of the gap between them.

## 4. Fair value (model-dependent)

### 4.1 Model

The forecaster, its inputs, and its estimation. Why this domain: what external
anchor makes an independent estimate credible.

### 4.2 Calibration

Reliability diagram, Brier score with its reliability/resolution decomposition,
log loss, and the Brier skill score against the base rate. Accuracy alone is
not reported; a forecaster that cannot beat the base rate has no business
sizing a position however accurate it looks.

### 4.3 Trading rule

The edge threshold, and how estimation uncertainty enters it. Trading a point
estimate ignores the standard error of the estimate and systematically
over-bets — state the adjustment used.

## 5. Fees

Kalshi's fee is a function of price and is not symmetric about 50¢. Derive it
explicitly rather than assuming flat basis points; show the formula and check
it against a settled trade. A flat-fee assumption will manufacture edge near
the tails.

## 6. Backtest

- Replay is chronological and uses only data available at each timestamp. State
  how this is enforced, not just that it holds.
- Fill model: what is assumed about crossing the spread, queue position, and
  partial fills against observed depth.
- Results: equity curve, drawdown, hit rate, and edge per trade — with the
  number of independent opportunities, since a Sharpe computed on a handful of
  correlated events means little.

## 7. Capacity

How much size the strategy absorbs before the edge disappears, given observed
book depth. This is the question that separates an interesting result from a
curiosity, and it should be answered with the depth data rather than asserted.

## 8. Why does this edge exist

Who is on the other side and why have they not closed the gap. Candidate
explanations for a retail-heavy venue: thin liquidity, favourite–longshot bias,
price-insensitive hedgers. Say which one this is, and what evidence supports it
over the alternatives.

## 9. What did not work

At least one strategy that failed, and the diagnosis. A write-up with no
failures reads as selection, not research.

## 10. Limitations

Fragile assumptions, sample-size limits, periods excluded and why, and what
would falsify the result.

## 11. Reproduction

Commands, seeds, data range, and package versions.
