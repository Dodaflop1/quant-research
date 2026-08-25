# Bucket-sum arbitrage: priced to a tenth of a cent, and the real test was never run

*2026-08-25. `scripts/detect_bucket_sum.py` over
`data/raw/kalshi_orderbook_2026-08-24.jsonl`, 189,201 snapshots, 110,967
complete baskets. Raw output in `results/kalshi/bucket_sum.json`.*

Two results, and the second is the one that matters.

1. **On two-leg events the market is priced to within 0.1c of the
   fee-adjusted no-arbitrage bound.** 5,792 baskets carried a bid sum above
   100c and **every one of them — 5,792 of 5,792 — was killed by fees.**
2. **The hypothesis this scan was built to test was never testable with the
   data collected.** The overround argument is about *large* fields. Of 6,218
   mutually-exclusive events in the metadata, the pinned universe covers **52**,
   of which 50 are two-leg. The three-leg events were complete **0% of the
   time**. No field of four or more legs was ever polled.

---

## 1. The two-leg result

| | |
|---|---|
| bid sum, median | **99.0c** |
| p90 / p99 / max | 100.0c / 101.0c / 103.0c |
| baskets with bid sum > 100c | **5,792** of 110,517 |
| of those, killed by fees | **5,792 (100%)** |
| closest miss | 100.0c against a 100.1c breakeven |

The closest baskets, with the shortfall against their own fee-adjusted
breakeven:

| event | bid sum | breakeven | short by |
|---|---|---|---|
| KXNCAAFGAME-26SEP05BALLOSU | 100.0c | 100.1c | **0.1c** |
| KXNCAAFGAME-26SEP05TNSTUGA | 100.0c | 100.1c | **0.1c** |
| KXGOVCA-26 | 100.3c | 100.6c | 0.3c |
| KXSENATEOKD-26 | 100.6c | 100.9c | 0.3c |
| KXMAPRIMARY-08D26 | **103.0c** | 103.5c | 0.5c |

**"No arbitrage found" undersells this.** The distribution does not sit safely
below the bound — it sits *on* it, 5,792 times, and is pushed back under by the
fee every time. The largest gross edge observed was 3.0c against a fee of 3.5c.

That is a much stronger claim than an absence, and it is the answer to "why
hasn't this edge been closed?": **it has been, and the fee schedule is what
closes it.** The exchange's fee is quadratic in price and rounds up per leg, so
a two-leg basket owes roughly `2 x ceil(0.07 x P(1-P))` — a floor of about
0.1c-3.5c depending on where the legs are priced. The bid sums track that floor
almost exactly.

### A cross-check worth noting

The median two-leg bid sum here is **99.0c**. The complementarity scan,
written independently, reported a median `yes_bid + no_bid` of **99.0c** on the
same tape. For a two-leg mutually exclusive event those are the same quantity —
A wins or B wins — so the agreement is a consistency check on both parsers, not
a second finding.

It also means **this scan learned nothing new about two-leg events.** The
complementarity result already contained it.

## 2. The real finding: the experiment was not possible

| legs | events | attempts | complete | complete % | median | max |
|---|---|---|---|---|---|---|
| 2 | 50 | 117,499 | 110,967 | **94.4%** | 99.0c | 103.0c |
| 3 | 2 | 2,522 | **0** | **0.0%** | – | – |
| 4+ | 0 | 0 | 0 | – | – | – |

**Not one three-leg basket was ever complete.** Two three-leg events were
polled 2,522 times between them and never once had all three legs quoted
simultaneously. Fields of four or more legs are not in the universe at all.

This matters because the overround finding — **243c summed across 293
families** — was measured on *large* fields, where the minimum tick props up
every longshot. That is where a short-side bucket sum should be most profitable,
and the scan has no data on it.

### Rough scale of what is untested

A large field with a bid sum near 200c collects 200c and owes at most 100c, so
gross edge is ~100c. Fees round up per leg, so a 20-leg basket owes at least
20c, leaving ~80c net on collateral of `100 x 20 - 200 = 1,800c` — a return on
collateral of roughly 4%. Not spectacular, but riskless and completely
unmeasured.

**This is an arithmetic sketch from the ask-side overround, not a measurement.**
Bid sums for large fields have never been observed. The number could be
anything.

## 3. Why the universe misses them, and the fix

`scripts/collect_kalshi.py` selects the top ~150 tickers by volume, then pins
them. Volume ranking picks *individual markets*, not *complete event families*.
A 20-leg field has its volume spread across 20 tickers, so few or none clear the
threshold, and any that do arrive as an incomplete basket — which this scan then
correctly refuses to evaluate.

**The fix is to select by event, not by ticker:** choose N mutually-exclusive
events and pin *every* leg of each. The same polling budget covers far fewer
events but makes each one evaluable. Given the two-leg result is already settled
by the complementarity scan, and the large-field case is entirely unmeasured,
the budget is currently spent on the question that is already answered.

Concretely, `discovery.py` should gain an event-complete selection mode:
given a leg budget, prefer whole families over high-volume orphans, and report
how many families fit.

## What can and cannot be claimed

**Can:** on two-leg mutually exclusive events in this universe, short-side
bucket-sum arbitrage does not exist net of fees, and the market sits within 0.1c
of the bound. 110,967 baskets, one day of tape, staleness bounded at 120 s,
partial baskets excluded, capacity taken from order-book depth.

**Cannot:** anything about large fields. The overround result stands as a
measurement of ask sums; whether it is harvestable on the short side is
untested, and the current collection design cannot test it.

## Actions

1. **Add event-complete universe selection** to `discovery.py` and recollect.
   This is the highest-value data change outstanding — it converts an untestable
   hypothesis into a testable one.
2. **Re-run this scan** once complete large fields exist in the tape.
3. **Cite the fee schedule as the mechanism** in the methodology write-up, with
   the 5,792/5,792 figure. It answers the "why does the edge not exist" question
   with evidence.
4. The two-leg result should be reported as a **consistency check** on the
   complementarity finding, not as an independent result.
