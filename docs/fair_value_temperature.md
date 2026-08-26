# Fair value for temperature contracts: the model, and why it will not quote yet

*2026-08-25. `src/quant/kalshi/models/temperature.py`,
`src/quant/kalshi/strategies/fair_value.py`, `scripts/probe_weather.py`.
43 tests in `tests/unit/test_temperature.py` and
`tests/unit/test_fair_value.py`.*

Structural arbitrage on Kalshi is closed. It is closed in the long direction at
the touch (ask sums 100.4–109.2¢), in the short direction after fees (5,792 of
5,792 baskets killed, closest miss 0.1¢), and at every field size (the gap
*widens* 9¢ → 140¢ as families grow). That is a real result and it is written
up, but it is a result about what is not there. The remaining half of Project 1
is a model that produces a price of its own.

This is that model, for one domain, and **it currently refuses to produce a
price.** That refusal is the design, not an unfinished edge.

---

## Why daily temperature

Three domains were candidates. The constraint that decided it is the number of
settled outcomes, because a fair-value model with no settled outcomes cannot be
scored, and an unscored model is an opinion.

| domain | free public forecast | settlements per year |
|---|---|---|
| Fed target rate | CME futures-implied | 8 |
| Daily high temperature | NWS, no key | ~365 per city, ~7 cities |
| Sports | closing lines, licensed | many, but the benchmark is the thing being beaten |

Fed rates give eight scoring opportunities a year, which is not a calibration
sample. Sports has volume, but the natural benchmark is the closing line, and a
model whose input is the sportsbook price is measuring the sportsbook. Weather
is the only one of the three where a genuinely independent public forecast
exists, settles daily, and can be scored hundreds of times before anyone has to
take the model's word for anything.

There is also an honest answer to the question an interviewer will ask, *why
does this edge exist* — the public forecast is a real information source, and
the retail flow on a daily temperature contract is not obviously pricing it
carefully. That is a hypothesis, not a finding, and the collection below is
what would test it.

## The two things that are easy to get wrong

**The forecast is a point; the contract is a distribution.** A forecast of 74°F
says nothing about the contract `72 to 73` until you know how far forecasts of
74 miss. That spread is the entire model. `ErrorModel` fits it per lead time
from (forecast, observed) pairs, and per station, because pooling a coastal
station's small errors with a continental one's large ones is wrong in both
directions.

**The observation is rounded and the bucket is not.** The station reports whole
degrees, so `72 to 73` is the event `round(T) ∈ {72, 73}`, which is
`T ∈ [71.5, 73.5)` — two degrees wide, not one. The obvious implementation,
`[72, 73]`, is half as wide and biases *every* bucket in the family the same
direction. Roughly a third of a day-ahead forecast error rides on that half
degree. There is a test that fails if anyone simplifies it.

A third thing turned up while testing: a far-tail bucket priced as a difference
of two CDFs underflows to **exactly zero**, because in the right tail the CDF
is 1.0 to every bit a float has. A zero prices a contract at 0¢ and invites an
unbounded position in something that can still settle YES. The distribution now
carries a survival function and buckets above the fitted centre are differenced
through it, so a probability of 1e-102 comes back as 1e-102.

## What the model refuses to do

`ErrorModel.fit` raises `UncalibratedModel` rather than return a model, when:

- a lead-time bucket has fewer than 30 pairs — it refuses instead of widening;
- every residual is identical, which in practice means duplicated rows rather
  than solved weather.

`bucket_probabilities` raises rather than normalise, when the family has a gap
or an overlap, or is closed at either end. This one is worth stating plainly:
a family with a missing degree produces raw probabilities that sum to less than
one, and normalising that away inflates every bucket by the same factor. The
result reads as **an edge in every contract at once**, which is the most
seductive possible bug in this project.

`evaluate` returns `None` rather than a signal, when the edge does not clear
the fee at the size proposed, when it does not clear two standard errors of the
fair value, when the relevant side of the book is empty, or when the size that
survives is under one contract.

## Fees and size are one problem, not two

The exchange rounds the *order* fee up, so the fee per contract falls with
size. One contract at 50¢ owes 2¢ on a fee of 1.75¢ — 4% of the position. A
hundred contracts owe 1.75¢ each. A per-contract edge quoted without a size is
therefore meaningless, and the sizing routine solves the fee and the size
together as a short fixed point rather than in sequence.

Sizing is quarter-Kelly on the probability moved **one standard error against
the position**, capped by the depth actually resting at the assumed price.
Those are two separate uses of the standard error and they do different jobs:
`edge_stderrs` decides *whether* to trade, `uncertainty_penalty_stderrs`
decides *how much*. Being careful about the first while sizing at the point
estimate is the common half-measure, and it over-bets exactly the trades the
model is least sure about.

## What this does not capture, stated before anyone asks

**The standard error is estimation risk, not model risk.** It propagates
uncertainty in the fitted bias and scale. It says nothing about whether the
error distribution has the right *shape*. A Gaussian model with a
well-estimated σ reports a *small* standard error on a tail bucket it is
systematically wrong about — the parameter uncertainty is smallest exactly
where the shape assumption is worst. There is a test asserting this holds, kept
as a regression on the documented limitation rather than on a bug.

This is the same distinction the diffusion side ran into and got wrong first: a
diagnostic that passes validates the fit, not the mechanism.

**Buckets are sized independently.** Contracts in one family are mutually
exclusive, so simultaneous YES positions in two of them are partly hedged and
simultaneous NO positions are correlated the other way. Correct joint sizing
needs the family covariance. Until that exists, `portfolio_cap_contracts`
bounds the family bluntly and the independence assumption is written down here
rather than buried.

## Why it cannot quote a price today

The error model needs **past forecasts paired with what happened**. The NWS API
serves the current forecast and recent observations; it does not serve its own
history. So:

1. **Collect forward.** One request per city per day for the seven-day
   forecast, one for the observed high. About 30 days to reach the 30-pair
   minimum per lead-time bucket per city, more to be comfortable.
2. **Pull an archive.** NOAA publishes model output on AWS Open Data with no
   key. Back-fills the forecast half immediately; heavier to wire up, but it
   means a fitted model this month rather than next.
3. **Assume a spread.** "Forecast errors are about 3°F." This produces a
   working system, a plausible backtest, and no way to know it is wrong.

`ErrorModel.fit` will not accept a hand-supplied σ, specifically so that the
third route stays closed. Four failures in this project came from building on
an unmeasured assumption that produced a plausible-looking number, and this is
the one place where such an assumption would be invisible in every downstream
result.

## What the first probe run established

Run 2026-08-25 against the live exchange and api.weather.gov. Three things
changed, and one of them would have quietly corrupted the model.

**The settlement source is not the observation feed, and for most series it is
not public either.** This is the finding that partly undercuts the domain
choice, so it goes first.

The help-centre article says daily temperature markets settle on the NWS Daily
Climate Report. Each series' own `settlement_sources` field says otherwise for
most of them:

| settles on | examples |
|---|---|
| The Weather Company (proprietary) | `KXHIGHNY`, `KXHIGHCHI`, `KXHIGHTPHX`, `KXHIGHTDAL`, most of the rest |
| NWS Daily Climate Report (free) | `KXHIGHOU`, `KXDENHIGH`, `KXPHILHIGH`, `KXDVHIGH`, legacy `HIGH*` |

The Weather Company product has **no public history**, so the observed half of
a (forecast, observed) pair cannot be retrieved for those series — and the
vendor is itself a forecaster, which means the settlement source and the
obvious model input are not independent. The claim in the section above, that
weather is the one domain with a genuinely independent free public forecast,
holds only for the NWS-settled subset. It was written from a help-centre page
rather than from the per-series field, which is the same mistake as reading a
payload's documentation instead of the payload.

The station observation endpoint is not a substitute for either: it returns
**Celsius**, and a maximum over hourly readings can miss a spike between them.
It agrees with the settled value most days and disagrees exactly on the days
the contract was interesting.

The whole-degree Fahrenheit assumption behind `Bucket.continuous_bounds`
survives in both cases.

**The CLI location id was in the output all along.** Fetching the climate
report failed with HTTP 400 twice, because the probe passed the station id
(`KNYC`) and then the issuing office (`OKX`). `/products/types/CLI/locations/`
wants the `issuedby` code, `NYC` — and Kalshi's own settlement URLs carry it:
`site=OKX&product=CLI&issuedby=NYC`. The probe now parses `issuedby` and `site`
out of those URLs and tries the contract's own ids first.

### The decision this forces

Three routes, and it is not obvious which is right:

1. **Restrict to the NWS-settled cities.** Keeps the free-data story whole, at
   the cost of a much smaller universe — a handful of cities rather than the
   dozen-plus assumed here.
2. **Model the Weather Company series anyway**, accepting a settlement source
   whose history is unavailable and whose publisher is a competitor forecaster.
3. **Take the observed half from Kalshi itself.** A settled event says which
   bucket won, which is the observed high interval-censored to the bucket
   width, measured in the settlement source's own units — and it works for both
   settlement sources. It leaves the *forecast* half still needing an archive,
   and it turns fitting into an interval-censored estimation problem rather
   than a plain one.

**The day is not the calendar day under Daylight Saving Time.** The report runs
midnight to midnight local in standard time, but **01:00 to 00:59 the following
day** under DST — eight months of the year. A collector that pairs forecasts
with calendar-day maxima is wrong for two thirds of the sample, and wrong in a
way that presents as forecast error rather than as a bug.

**The probe's own first answer was wrong, and quietly.** It paged `/events`,
stopped at its page cap 2,400 events in, printed the count, and reported no
temperature series — while the cursor was still live. It also matched series on
substrings, which put a Kelowna mayoral election, a Lowe's earnings market and
an NFL ownership market in a temperature report, all on the "LOW" inside
another word. Series are now enumerated through `GET /series`, selected by the
category the exchange itself sets, and every paged loop reports whether it
finished or hit its cap. There are four tests on that last part, because
"nothing found" and "stopped looking" reading the same is the exact shape of
the mistake this project keeps making.

## Next, in order

1. **Rerun `scripts/probe_weather.py`** now that it enumerates properly, and
   read the settlement source and bucket structure off a real temperature
   series.
2. **Write the pair collector against the measured shapes**, not before. Two
   requests per city per day: the seven-day forecast, and the Daily Climate
   Report for the day just closed. Respect the DST window.
3. **Fit, and score against the settled outcomes** with the calibration module.
   The scoring question is not "does the model make money" but "is it better
   calibrated than the price", and the Brier decomposition already separates
   the two ways it could be.
