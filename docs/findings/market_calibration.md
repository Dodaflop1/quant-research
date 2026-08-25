# The market is not grossly overconfident. That is all this says.

*2026-08-25. `scripts/market_calibration.py`, 398 settled markets across 12
series, prices read from the trade tape at fixed horizons before close. Raw
output in `results/kalshi/market_calibration.json` and
`results/kalshi/settled_prices.json`.*

The purpose was to measure the benchmark before building a fair-value model to
beat it. **Calibration is not rejected** — Spiegelhalter z = +0.20, p = 0.845 at
one hour before close, with no reliability bin significantly off the diagonal.

**That result is far weaker than it looks, and the power analysis is the finding.
This sample has 7% power against a 30% favourite-longshot bias.** "Not rejected"
here means "we could not have detected it either way", not "it is not there".

---

## The scores

| horizon | n | base | Brier | log loss | skill | REL | RES | z | p |
|---|---|---|---|---|---|---|---|---|---|
| 1h | 398 | 0.352 | 0.1064 | 0.3310 | +0.534 | 0.0029 | 0.1245 | +0.20 | 0.845 |
| 6h | 379 | 0.354 | 0.1498 | 0.4370 | +0.345 | 0.0086 | 0.0874 | +1.84 | 0.066 |
| 24h | 243 | 0.284 | 0.1529 | 0.4635 | +0.248 | 0.0203 | 0.0718 | +1.45 | 0.146 |

Reliability at one hour, bins with 20 or more outcomes:

| range | n | forecast | observed | gap |
|---|---|---|---|---|
| 0.00–0.10 | 151 | 0.022 | 0.020 | −0.002 |
| 0.10–0.20 | 32 | 0.145 | 0.094 | −0.052 |
| 0.20–0.30 | 34 | 0.253 | 0.206 | −0.047 |
| 0.30–0.40 | 30 | 0.348 | 0.367 | +0.018 |
| 0.40–0.50 | 22 | 0.440 | 0.545 | +0.105 |
| 0.60–0.70 | 25 | 0.655 | 0.720 | +0.065 |
| 0.80–0.90 | 23 | 0.837 | 0.696 | −0.141 |
| 0.90–1.00 | 56 | 0.968 | 0.946 | −0.022 |

Every forecast lies inside its bin's Wilson interval. Nothing here is
individually significant.

## The power analysis, which is the point

Simulating outcomes from a *deliberately miscalibrated* truth and scoring the
quoted prices against them, 2,000 replications at α = 0.05:

| miscalibration | power at n = 398 |
|---|---|
| none (the false-positive rate) | 5% |
| every price 2c too high | 5% |
| every price 5c too high | **6%** |
| longshots overpriced 30% (favourite-longshot) | **7%** |
| longshots overpriced 50% | 13% |
| prices pushed 20% toward the extremes | **99%** |
| prices pushed 40% toward the extremes | 100% |

**The test is near-blind to exactly the biases prediction markets are known
for, and nearly certain to catch the one they are not.**

### Why, and it is structural

Spiegelhalter's statistic weights each forecast by `(1 − 2p)`. It is built to
detect a **spread** error — over- or under-confidence — and a location shift
barely moves it. Both the favourite-longshot bias and a uniform overpricing are
location shifts. The mean `|1 − 2p|` on this sample is 0.698, so the weighting
is not the problem; the statistic simply is not aimed at these alternatives.

The reliability term is the one that sees location error, and on this sample it
barely moves either: 0.0041 as observed against 0.0045 with longshots 30%
overpriced. At n = 398 that difference is noise.

### What sample size would settle it

Bootstrapping the observed price mix:

| n | favourite-longshot (30%) | uniform 5c |
|---|---|---|
| 400 | 7% | 6% |
| 1,000 | 13% | 8% |
| 3,000 | 32% | 15% |
| **10,000** | **84%** | 41% |
| 30,000 | 100% | 88% |

**Roughly 10,000 settled markets to detect a favourite-longshot bias at 80%
power, and 30,000 for a 5c uniform bias.** That is achievable — the exchange
settles that many — but it is a collection job of a different size, and it is
the honest prerequisite for any claim about mispricing.

## What can and cannot be claimed

**Can:** Kalshi's sports markets are **not grossly overconfident** at one hour
before close. Prices pushed even 20% toward the extremes would have been caught
with 99% probability, and were not. Skill against climatology is +0.534.

**Cannot:**

1. **That the market is calibrated.** The test could not have found a 30%
   favourite-longshot bias if it existed. This is the distinction between
   failing to reject and confirming, and it is the whole result.
2. **Anything about the exchange as a whole.** The 12 series are
   `KXLOWTLV`, `KXLMBGAME`, `KXWTADOUBLES`, `KXKLEAGUEGAME`, `KXLOWTHOU`,
   `KXHNLGAME`, `KXDENSUPERLIGAGAME`, `KXSWISSLEAGUEGAME` and four with fewer
   than five markets each — tennis, baseball, Korean, Danish and Swiss football.
   **Not one politics, economics or Fed market.** These are the series that both
   settle frequently and trade enough to clear a 100-contract volume floor. The
   flagship long-dated markets settle too rarely to appear.
3. **That skill of +0.534 means the market is smart.** 52% of prices sit outside
   10–90c one hour before close. Most of these events were nearly decided, and
   predicting a near-decided outcome is easy. The skill figure is substantially
   the clock.
4. **Anything from the horizon table.** Only 6 of 398 markets have a price at
   every horizon, so the common-sample control could not run. The apparent
   decay from +0.534 to +0.248 is confounded with which markets happened to
   trade a day out.

## Two sampling failures worth recording

Both produced numbers that looked fine and were not.

**The settled feed cannot be sampled.** Paging `/markets?status=settled` returns
**98.9–100% auto-generated MVE shards** — machine-made parlay combinations,
created and settled minutes apart with zero volume. Ten pages spanned eight
minutes of close times. The fix was to invert the query: enumerate real series
from the live universe and ask for settled markets *within* each.

**Series must be sampled, not sliced.** The first working run took 120 series
from a `sorted()` list and produced 210 markets that were **79% Australian rules
football and 21% one political fundraising series**. It reported the market
"systematically over-confident" at p = 0.037, with a longshot bin containing a
single event. Shuffling with the seed before truncating fixed it.

The script now prints sample composition — distinct series, largest share,
fraction priced outside 10–90c — **above** the scores, and refuses a verdict
when fewer than 10 series contribute or one exceeds 40%. A concentration
warning that appears below the numbers is a concentration warning nobody reads.

## Actions

1. **Do not use these numbers to justify a fair-value model.** They neither
   support nor rule out the bias such a model would target.
2. **Scale the collection to ~10,000 settled markets** if the favourite-longshot
   question is worth answering. One evening of API time at 2 req/sec.
3. **Report the reliability term, not the z-test, when the question is location
   bias.** The z-test answers a different question, and quoting its p-value as
   evidence of calibration is the mistake this document exists to prevent.
4. **The sports-only composition is a real limit.** Any statement about Kalshi
   pricing from this sample is a statement about niche sports markets.
