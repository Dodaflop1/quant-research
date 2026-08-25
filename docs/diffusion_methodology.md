# Information diffusion and reflexivity in event contracts — methodology

*Last updated 2026-08-25. Sections are written as results arrive. Anything not
yet done says so.*

**Nothing here has been fitted to real data yet, and that is deliberate.** A
Hawkes process will return a branching ratio for any point process you hand it.
The number is worthless until the estimator itself has been characterised, so
this document begins with what the estimator does to data whose answer is
already known. That work has already produced the result that constrains
everything downstream: **a constant-baseline Hawkes fit reports a branching
ratio of 0.79 on data containing exactly zero self-excitation.**

---

## 1. Objective

Two hypotheses, on the same exchange, with the same estimator and the same
diagnostics.

**H1 (slow).** Trade arrivals in long-dated event contracts exhibit
self-excitation over horizons of hours, consistent with information diffusing
through a population of traders rather than with microstructural feedback. The
branching ratio is materially below 1 and materially above the finite-sample
noise floor of 0.19 established in §4.3.

**H2 (fast).** Trade arrivals in the aggregate exchange tape exhibit
self-excitation over horizons of seconds, and the estimated branching ratio
approaches the 0.8–0.9 range reported for equity order flow.

**The contrast is the contribution.** If H2's branching ratio is near-critical
while H1's is modest, measured on the same venue with one estimator, that is
evidence about *what published near-unity estimates are measuring* — and it
speaks directly to the objection that such estimates reflect non-stationary
baselines and kernel misspecification rather than genuine reflexivity.

Each hypothesis is stated so it can fail: a branching ratio inside the noise
floor, or diagnostics that reject the fit, falsifies it.

### 1.1 What changed from the original scope, and why

The project was scoped as Reddit mentions → equity price discovery. Two
measurements redirected it.

First, Kalshi trade prints turned out to be **backfillable** (`/markets/trades`
for roughly three months, `/historical/trades` beyond), so an order-flow study
needed no six-week accumulation and no third-party API approval.

Second, and more usefully, measuring trade density showed the study needed
*two* datasets rather than one. See §2.3.

## 2. Data

### 2.1 Sources

| Arm | Source | Span | Events |
|---|---|---|---|
| Slow | 150 pinned Kalshi markets | 2026-05-28 → 2026-08-25 (89.1 days) | 151,477 trades |
| Fast | Kalshi exchange-wide tape | 2026-08-24, 9h13m | 3,560,000 trades |

Each print carries `created_time`, price, size, and — importantly —
`taker_outcome_side`, so aggressor direction is **observed rather than inferred**.
No Lee–Ready style classification is needed, which removes a standard source of
error in order-flow studies.

### 2.2 Direction convention

Taker side is recorded in **YES terms throughout**. A taker who bought NO is
recorded as a YES seller: buying NO at *q* and selling YES at *100 − q* are the
same trade viewed from opposite sides of the book. Collapsing both into one
signed direction is what makes buy- and sell-initiated flow comparable across
markets, and it is a prerequisite for the bivariate fit in §6.

Getting this backwards would invert every cross-excitation term while leaving
univariate results untouched — a bug that stays hidden until the interesting
result. It is covered by unit tests.

**Observed imbalance: 63% buy-initiated, 37% sell-initiated** (95,538 vs 55,939
over the slow arm). Consistent with the documented preference for buying YES
over selling it in prediction markets. A bivariate model should reproduce this
rather than assume it away.

### 2.3 Density is the reason there are two arms

| | Slow arm | Fast arm |
|---|---|---|
| Median market | 162 trades / 90 days | — |
| Busiest market | 16,759 trades = **8/hour** | — |
| Aggregate | 1,701 trades/day | **387,000 trades/hour** |
| Markets ≥ 2,000 trades | 13 of 150 | — |
| Markets with **zero** trades in 90 days | 2 | — |

A factor of roughly **48,000** separates the busiest long-dated market from the
exchange aggregate, and the top 14 markets hold 70% of the slow arm's volume.

This is not a data-quality problem, it is a physical one. At 8 trades/hour,
arrivals are 7.5 minutes apart on average. Any excitation detectable at that
spacing operates over **hours**, and calling it microstructural reflexivity would
be a category error. Equally, the dense tape cannot support a study of long-dated
contracts because those markets are not where the volume is.

The two arms are different phenomena that happen to share an estimator. Treating
them as one dataset would average them into meaninglessness.

### 2.4 Deduplication

Trades near the rolling live/historical cutoff are fetched **twice by design** —
both endpoints are queried over every window rather than hardcoding a cutoff date
that drifts. Parsing deduplicates on `trade_id`; **7,287 duplicates were removed**
from the slow arm. Counting them twice would inflate precisely the arrival rate
the study measures.

### 2.5 Timestamp accuracy

Server-assigned `created_time` at sub-second resolution. No client-side clocks
are involved in the arrival series, which removes the usual timestamp-error
caveat on lead–lag work — though see §7 on what precedence can and cannot show.

### 2.6 Known gaps

- The fast arm is **one partial day** (9h13m). It is dense enough to fit but too
  short for any claim about stability across days or regimes.
- The slow arm's panel is **deliberately biased** toward long-dated markets and
  is not representative of Kalshi.
- Order book snapshots and trade prints cover the same 150 markets but begin at
  different times; the books start 2026-08-25, the trades reach back to May.

## 3. Model

Univariate Hawkes with exponential kernel:

```
lambda(t) = mu + sum_{t_i < t} alpha * exp(-beta * (t - t_i))
```

- `mu` — baseline intensity, the rate of immigrant events
- `alpha` — excitation amplitude, the intensity jump caused by one event
- `beta` — decay rate of that excitation

The branching ratio is the integral of the kernel:

```
n = integral_0^inf alpha * exp(-beta * s) ds = alpha / beta
```

`n` is the expected number of direct offspring per event; the process is
stationary iff `n < 1`; mean cluster size is `1 / (1 - n)`; excitation half-life
is `ln(2) / beta`. **`mu` does not enter `n`** — the baseline governs how many
clusters start, not how strongly each event excites the next.

All rates are reported **per second** unless stated otherwise. `mu`, `alpha` and
`beta` are rates and are meaningless without the unit; a branching ratio is
dimensionless and is not.

In the implementation, `n`, stationarity, half-life and cluster size are computed
properties rather than stored fields, so they cannot drift out of sync with the
parameters they derive from.

**Why the exponential kernel.** It admits the recursion
`A_i = exp(-beta*(t_i - t_{i-1})) * (1 + A_{i-1})`, which turns an O(n²)
likelihood into O(n). A power-law kernel fits reflexivity data better and costs
the recursion; that trade-off is revisited in §8.

## 4. Estimation and validation

MLE via `scipy.optimize` (L-BFGS-B) on log-parameters, multi-start across four
decades of timescale, with box constraints derived from identifiability limits.
Standard errors from a central-difference Hessian, mapped to natural parameters
by the delta method.

**Stationarity is not constrained.** A fitted `n ≥ 1` is reported as-is. It is
the single most informative diagnostic the fit produces — usually about a
misspecified kernel or a window spanning a regime change — and constraining it
away replaces a finding with a boundary solution that looks like a result.

### 4.1 Parameter recovery

Truth `mu=0.5, alpha=0.8, beta=1.6` (`n = 0.5`), T = 2000, ~2000 events per
replication, 40 replications, non-converged and boundary fits excluded and
counted.

| Parameter | Truth | Mean estimate | Bias | Mean SE | Empirical SD | 95% coverage |
|---|---|---|---|---|---|---|
| `mu` | 0.5000 | 0.4921 | −1.6% | 0.0259 | 0.0205 | 95.0% |
| `alpha` | 0.8000 | 0.7800 | −2.5% | 0.0622 | 0.0685 | 85.0% |
| `beta` | 1.6000 | 1.5435 | −3.5% | 0.1356 | 0.1375 | 90.0% |

Branching ratio: truth 0.5000, mean 0.5059, SD 0.0246.

**Coverage is the column that matters.** Point estimates can look fine while the
standard errors are fiction, and it is the standard errors that decide whether
0.62 differs meaningfully from 0.55. Coverage for `alpha` at 85% is below
nominal — the asymptotic errors are mildly optimistic at this sample size, and
intervals on `alpha` should be read as slightly narrow.

### 4.2 A bug this study caught that a single fit could not

Unbounded log-parameters let L-BFGS-B walk into the region where `exp()`
overflows. The objective there returned a flat penalty, so the gradient was
exactly zero and the optimiser **reported success** at `alpha ≈ 10¹⁹⁰`.

It was invisible in a single 20,000-event fit, which converged cleanly and
recovered all three parameters to within 1.5 standard errors. It surfaced only
as infinite variance across replications at 2,000 events.

This is the argument for replication studies in one sentence, and it is why
boundary solutions are now reported as non-convergence with their standard
errors suppressed.

### 4.3 Negative control — Poisson

True `n = 0` exactly. 38 converged fits on homogeneous Poisson arrivals:

- mean 0.064, **median 0.033, 95th percentile 0.192**

The 95th percentile is the **operational noise floor**. A branching ratio below
roughly 0.19 on a comparable sample is not evidence of anything.

### 4.4 Seasonality contamination — the constraining result

Inhomogeneous Poisson with a sinusoidal rate, `lambda(t) = r(1 + 0.8 sin(2πt/P))`.
Every arrival is independent given the rate function. **There is no
self-excitation whatsoever.**

40 converged fits:

- Branching ratio **mean 0.791, median 0.788, max 0.812**
- Residual diagnostics rejected the fit in **5 of 40 cases (12%)**

A constant-baseline Hawkes has no other way to explain clustering, so it absorbs
the varying rate as excitation. The number it invents — 0.79 — sits squarely
inside the 0.8–0.9 range published for equity order flow, and the standard
goodness-of-fit machinery misses it seven times out of eight.

**Consequence, binding on all subsequent work.** Kalshi flow has obvious intraday
and event-driven seasonality. A branching ratio fitted to raw trade times
inherits this bias and is not reportable. The real fit must either estimate a
time-varying baseline or use windows short enough that the baseline is locally
flat, and **must report the branching ratio both ways.**

## 5. Goodness of fit

Under the time-rescaling theorem, if the fitted intensity is correct then the
compensator-transformed inter-arrival times are i.i.d. unit-rate exponential.

The theorem makes **two** claims, so two tests are reported:

- **Kolmogorov–Smirnov** against Exp(1) — tests the marginal distribution
- **Ljung–Box** on the rescaled series — tests independence

KS cannot see autocorrelation. A misspecified kernel can produce
correctly-distributed but autocorrelated residuals, and reporting KS alone is the
standard way a Hawkes fit passes validation it should have failed. A fit is
recorded as passing only if **neither** test rejects.

Two caveats stated rather than buried:

- The parameters were estimated from the same data, so the KS p-value is
  optimistic. **A rejection is decisive; a pass is weak evidence.**
- An underpowered test fails to reject almost anything, so the residual count is
  reported alongside every p-value.

On a correctly-specified 20,000-event sample the diagnostics behave: rescaled
interval mean 0.9999 and variance 1.0052 against 1.0 under the null, KS
D = 0.0036 (p = 0.95), Ljung–Box Q = 23.2 (p = 0.28). On deliberately wrong
parameters they reject, which is the property that makes a passing result mean
anything.

Comparison against a homogeneous Poisson null on held-out likelihood is **not yet
implemented** and is required before any fit is reported.

## 6. Bivariate extension — not yet implemented

Buy-initiated and sell-initiated flow as two coupled processes with
cross-excitation. The aggressor side is already parsed and tested (§2.2), so this
is modelling work rather than data work. The question is whether buying begets
buying (momentum) or begets selling (mean reversion), and whether the answer
differs between the two arms.

## 7. Link to prices — not yet implemented

The intended design, stated now so it can be criticised before it is run:

Kalshi's scheduled-resolution contracts give an unusually clean identification.
Fed decisions, CPI and jobs releases have **announcement times known in
advance**, so the arrival of information is exogenous in a way an unscheduled
headline is not. The event-study window is defined on those times.

**Causal versus correlational.** Three distinct claims are available — that
diffusion *causes* price moves, that it *correlates* with them, or that it *lags*
them. The scheduled subsample is what separates them; without it, order flow and
price are both driven by the same underlying news and no amount of Granger
testing distinguishes the cases. Granger tests precedence, not causation.

**A structural advantage over the original equity design worth stating:** an
event contract has a *known terminal value*. The price must converge to 0 or 100,
so "price discovery" is measurable as distance-to-settlement over time rather
than inferred from a benchmark model. There is no market model, no FF3, and no
abnormal-return specification to argue about.

Standard errors will be clustered by event, since overlapping windows on
correlated contracts are not independent observations.

## 8. Limitations

- **No real-data fit exists yet.** Everything above is estimator
  characterisation.
- **The 0.79 seasonality artefact is not yet corrected for**, only measured. Until
  a deseasonalised fit exists, no branching ratio from this project is
  reportable.
- **CI coverage on `alpha` is 85%, not 95%** (§4.1). Intervals on the excitation
  amplitude are narrower than they should be.
- **The exponential kernel is chosen for tractability.** Reflexivity data is
  generally better fitted by a power law, and the Hardiman–Bouchaud critique
  implicates kernel misspecification directly. Reporting a power-law fit as a
  robustness check is outstanding work, not optional polish.
- **The fast arm is one partial day**, so nothing can be said about stability.
- **Marks are ignored.** The current model treats every trade as identical
  regardless of size. A 1-contract trade and a 1,000-contract trade excite the
  process equally, which is unlikely to be true.
- **Two markets in the slow arm have zero trades in 90 days**, and the median
  market has 162. Pooling thin markets is necessary and introduces a
  heterogeneity assumption not yet tested.

## 9. Reproduction

```bash
# estimator validation: recovery, negative control, seasonality
python scripts/hawkes_recovery.py --reps 100

# trade prints for the pinned universe
python scripts/backfill_trades.py --days 90 --tickers-file ./data/universe.txt
python scripts/backfill_trades.py --report --out ./data
```

All simulation takes an explicit `seed`; a simulation study whose numbers cannot
be reproduced is an anecdote. Results are written to
`results/diffusion/hawkes_validation.json`. Python 3.12, numpy 2.x, scipy 1.x.
Estimator, simulator and diagnostics are covered by the unit suite
(`pytest tests/unit`).

## References

- Filimonov & Sornette (2012), *Quantifying reflexivity in financial markets*
- Hardiman, Bercot & Bouchaud (2013), *Critical reflexivity in financial markets:
  a Hawkes process analysis*
- Bacry, Mastromatteo & Muzy (2015), *Hawkes processes in finance*
