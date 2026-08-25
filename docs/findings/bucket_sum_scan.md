# Bucket-sum arbitrage does not exist here, and the fee schedule is why

*2026-08-25. `scripts/detect_bucket_sum.py` over 189,201 order book snapshots
(110,967 complete baskets) and `scripts/field_size_scan.py` over 5,395 live
families. Raw output in `results/kalshi/bucket_sum.json` and
`results/kalshi/field_size_scan.json`.*

**Every direction, every field size, closed — with the mechanism measured.**

1. **On two-leg events the market is priced to within 0.1c of the fee-adjusted
   bound.** 5,792 baskets carried a bid sum above 100c and **every one of them —
   5,792 of 5,792 — was killed by fees.**
2. **Large fields are further from arbitrage than anything else on the
   exchange.** The requirement rises as `100 + N` (103c at two legs, 174c at
   fifty-plus) while the bid sum *falls* (94.0c → 54.0c). The gap widens
   monotonically to **140c**. Not one of 5,395 families is inside a tradeable
   short basket.
3. **The overround is bid-ask spread, not mispricing.** Summed spread goes from
   11.3c at two legs to **486c at fifty-plus, a 43× increase.** The minimum tick
   props up the ask on every longshot; the bid is simply absent.

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

## 2. Large fields — measured, and the question is closed

**An earlier version of this document said the large-field case was untested and
that the fix was event-complete universe selection. Both were wrong.** The claim
rested on an arithmetic sketch of mine assuming a large field's bid sum would be
around 200c. It is not, and one scan settled it.

`scripts/field_size_scan.py` runs `discover()` once with `MAX_TRADEABLE_LEGS`
disabled and tabulates the short-side economics by field size. **5,518 families,
5,395 with a bid on at least 80% of legs** — a hundred times the 52 families in
the pinned collection universe.

| legs | families | median bid sum | required | median short gap | median spread |
|---|---|---|---|---|---|
| 2 | 2,465 | **94.0c** | 103.0c | 9.0c | 11.3c |
| 3–4 | 1,680 | 87.0c | 106.0c | 19.0c | 25.0c |
| 5–9 | 460 | 89.7c | 108.0c | 18.6c | 25.6c |
| 10–19 | 377 | 78.0c | 115.0c | 38.0c | 70.0c |
| 20–49 | 324 | 85.0c | 130.0c | 47.0c | 92.0c |
| **50+** | 89 | **54.0c** | **174.0c** | **140.0c** | **486.0c** |

**Not one family of any size is inside a tradeable short basket.** The closest is
1.0c short, and it is two legs.

### The two quantities move apart, not together

The short basket needs `100 + N` cents. That requirement rises with field size by
construction: **103c at two legs, 174c at fifty-plus.** The sketch assumed the
bid sum would rise faster.

**It falls.** 94.0c → 54.0c. So the gap widens monotonically — 9c, 19c, 18.6c,
38c, 47c, **140c** — and a fifty-leg field is not close to arbitrage, it is
further from it than anything else on the exchange.

### Why: the overround is spread, not mispricing

The median summed spread goes from **11.3c at two legs to 486c at fifty-plus, a
43× increase.** That is the whole of the overround.

In a large field the minimum tick props up the *ask* on every longshot — you
cannot quote below 1c — so ask sums inflate far above 100c. The *bids* on those
same longshots are simply absent: nobody rests capital at 1c for a 1% outcome,
as the fee analysis in section 3 shows they would be paying a 7% fee to do. So
the ask side inflates, the bid side hollows out, and the two diverge.

**The 243c overround measured across 293 families was an ask-side number.** It
was never harvestable, in either direction: the long basket pays 243c to receive
100c, and the short basket collects 54c against a 174c requirement.

### Consequence

`MAX_TRADEABLE_LEGS = 10` is not merely correct — it is generous. The comment
above it in `discovery.py` already carried the right argument and one measured
family (`KXPGATOUR-BMC26`, 184 legs, bid sum 98.9c against 150c required). This
scan turns that anecdote into a distribution over 5,395 families.

**No universe change is needed, and the event-complete selection work is
cancelled.** The structural-arbitrage half of Project 1 is now a complete
negative result: both directions, every field size, with the mechanism named and
measured.

## What can and cannot be claimed

**Can:** on two-leg mutually exclusive events in this universe, short-side
bucket-sum arbitrage does not exist net of fees, and the market sits within 0.1c
of the bound. 110,967 baskets, one day of tape, staleness bounded at 120 s,
partial baskets excluded, capacity taken from order-book depth.

**Can:** on large fields, that the short basket gets monotonically worse with
size, across 5,395 live families with a bid on at least 80% of legs. The
mechanism is arithmetic — the fee floor rises as `100 + N` while the bid sum
falls — so it is not a snapshot artifact.

**Cannot:** that no dislocation could ever occur. These are quoted states at one
moment, and a two-leg family sitting 1.0c short would need only a one-tick move
to cross. What is established is that the *structural* overround is not the
source of it, and that field size is the wrong place to look.

## Actions

1. ~~Add event-complete universe selection~~ — **cancelled.** The scan it was
   meant to enable has been run a cheaper way and answered the question.
2. **Cite the fee schedule as the mechanism** in the methodology write-up, with
   the 5,792/5,792 figure and the field-size table. Together they answer "why
   does this edge not exist, and why has nobody closed it" with evidence rather
   than a story.
3. The two-leg detector result is a **consistency check** on the complementarity
   finding, not an independent result — same quantity, same 99.0c median.
4. **Move Project 1 to fair value.** Structural arbitrage is finished as a line
   of enquiry. What remains untouched is the model-dependent half: one domain,
   calibrated, with Brier decomposition and a reliability diagram.
