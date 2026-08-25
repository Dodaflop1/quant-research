# YES/NO complementarity, verified

*2026-08-25. `scripts/verify_complementarity.py` over
`data/raw/kalshi_orderbook_2026-08-24.jsonl`, 189,201 snapshots across 150
pinned markets. Full output in `results/kalshi/complementarity.json`.*

**The identity holds.** Every price in Project 1 is computed from a YES-terms
book that `parse_orderbook` builds by converting NO bids into YES asks with
`ask = 100 - no_bid`. That line had been assumed, never checked. It is now
checked, and it is sound.

Two further things fell out of the check that were not what it was looking for.

---

## 1. The identity — verified

| | |
|---|---|
| two-sided snapshots | **108,489** |
| crossed books (`yes_bid + no_bid > 100.5`) | **0** |
| median `yes_bid + no_bid` | **99.0c** (a one-cent book) |
| p1 / p99 | 95.0c / 99.9c |
| price range observed | 0.10c – 99.00c, inside [0, 100] |
| payload shape | `yes_dollars` / `no_dollars`, 189,201 / 189,201 |

Not one snapshot in 108,489 has the two bid ladders overlapping. A crossing
would have meant either the identity is wrong, the snapshot mixes stale prices
across the two sides, or there is a riskless trade sitting on the book; none
occurs. The 99c median bid sum also reconciles with the previously measured ask
sums of 100.4–109.2c, since `ask_sum = 200 - bid_sum`.

Consequence: **every spread, touch price, fee figure and arbitrage estimate in
Project 1 rests on a verified identity rather than an assumed one.**

## 2. 30% of the panel is not quoted at all

| snapshot state | count | share |
|---|---|---|
| both ladders present | 108,489 | 57.3% |
| YES only — **no ask** | 11,532 | 6.1% |
| NO only — **no bid** | 11,532 | 6.1% |
| **no book at all** | 57,648 | **30.5%** |

The empty ones concentrate in markets that were never quotable during the
window, not in markets that went quiet. `KXNCAAFGAME-26SEP05BALLOSU-OSU` is a
5 September college football game polled on 24 August: open, listed, and
untouched for all 1,261 snapshots. Same for the Czech and Uruguayan league
fixtures and a Massachusetts primary.

`--min-days-to-close` removed markets that had already *settled*. Nothing
removes markets that have not started trading. **The effective panel is ~86
markets, not 150**, and any statistic computed "across markets" that silently
skipped empty books was computed on a self-selected subset. This belongs in the
methodology doc as a stated limitation, and the universe filter should gain a
liveness criterion — require a two-sided quote in some fraction of recent
snapshots, not merely an unsettled close date.

## 3. The one-sided markets are settled in all but name — and untradeable

The one-sided counts came out **exactly equal**: 11,532 YES-only and 11,532
NO-only. Across 189,201 snapshots that is not chance, and it was worth chasing
before trusting anything else in the file.

It is structural, and benign. Ten tickers are always YES-only, ten always
NO-only, the two sets are disjoint, and **8 of 10 events contribute a ticker to
each group**:

| event | YES-only leg | NO-only leg |
|---|---|---|
| `KXNCAAFGAME-26AUG29STETSDST` | `-SDST` @ 99c | `-STET` @ 99c |
| `KXNCAAFGAME-26SEP03ARPBMIZZ` | `-MIZZ` @ 99c | `-ARPB` @ 99c |
| `KXMAPRIMARY-03D26` | `-LTRA` @ 98c | `-GCLA` @ 99c |
| `KXPRIMARYPLACE-SENATEMID26-2` | `-HSTE` @ 99c | `-MMCM`, `-AELS` @ 99c |
| `KXWIDGOV2ND-GOVWINOMD26-2` | `-FHON` @ 99c | `-DCRO`, `-MBAR` @ 99c |
| `KXCOUNTYCHAMPMATCH-…KENMID` | `-KEN` @ 99c | `-MID` @ 99c |

Every populated side sits at **98–99c**. "SDST wins, bid 99" and "STET loses,
bid 99" are the same statement about the same game. The exact 11,532 equality is
then just ten tickers on each side sharing a polling cadence: 1,262 + (1,261 x 8)
+ 182 on both.

Nobody rests a bid at 1c, so the cheap side of a near-certain market has no
resting interest and the book is one-sided by economics rather than by error.

### Why that matters: the only executable trade is negative-EV by construction

In a market with a 99c YES bid and an empty NO ladder, `yes_ask = 100 - no_bid`
does not exist. **There is no offer.** The single executable trade is to hit the
99c bid — sell YES at 99, which is buying the 1% outcome at 1c.

Kalshi's taker fee is `ceil(M x 0.07 x C x P x (1-P))`, quadratic in price and
**rounded up per order**:

| contracts | cost | fee | total | EV | EV per contract |
|---|---|---|---|---|---|
| 1 | 1c | 1c | 2c | **−1.0c** | −1.000c |
| 10 | 10c | 1c | 11c | −1.0c | −0.100c |
| 100 | 100c | 7c | 107c | −7.0c | −0.070c |
| 1,000 | 1,000c | 70c | 1,070c | −70.0c | −0.070c |

At a fair price of 1c the trade is exactly break-even before fees, so the fee is
the whole result: **7% of notional at the tails**, against 3.5% at 50c.

| price | fee on 100 contracts | as % of cost |
|---|---|---|
| 1c | 7c | **7.0%** |
| 25c | 132c | 5.3% |
| 50c | 175c | 3.5% |
| 75c | 132c | 1.8% |
| 99c | 7c | **0.1%** |

The fee is *symmetric in absolute cents* around 50c — `P(1-P)` is — but as a
share of what you pay it is worst where the price is lowest. That is the
mechanism behind the direction asymmetry already recorded: the cheap tail, which
is where mispricing is easiest to spot, is exactly where the fee eats the edge.

**This is a direct observation of the fee ceiling, not an inference from the
formula.** Ten markets in the panel are quoted such that the only trade
available is one whose fee equals or exceeds its expected value.

---

## Actions

1. **Universe filter needs a liveness criterion.** Unsettled is not the same as
   quotable. Require a two-sided quote in some fraction of recent snapshots.
2. **State the effective panel size.** ~86 of 150, and say which analyses ran on
   the subset.
3. **Exclude the 98-99c markets from any signal search**, and say why: no offer,
   and the only executable trade is fee-negative. Their presence in a
   "detected opportunity" count would be a bug.
4. `parse_orderbook`'s `ask = 100 - no_bid` can now be cited as verified rather
   than assumed. It should carry a reference to this document.
