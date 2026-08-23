# Information diffusion model — methodology

> Skeleton. Each section states what has to go in it and the question a reader
> will ask. Sections are written as results arrive, not at the end.

## 1. Objective

The hypothesis, stated so it can fail. "Social mentions and returns are
related" is not a hypothesis; a claim about the sign, horizon and magnitude of
a specific relationship is.

## 2. Data

- Event calendar: source, and how announcement times are established. Scheduled
  events matter because the arrival of information is exogenous in a way an
  unscheduled headline is not.
- Social: platform, communities, collection window, and how tickers are
  extracted. Report the false-positive rate of ticker extraction on a hand-
  labelled sample — `$GME` is easy, bare `A` or `IT` or `ON` is not.
- Market data: resolution, provider, and how bars are aligned to event times.
- Deletions and edits: posts that vanish are not missing at random.

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
stationary iff `n < 1`, and mean cluster size is `1 / (1 - n)`. Note that `mu`
does not enter `n`: the baseline governs how many clusters start, not how
strongly each event excites the next. Excitation half-life is `ln(2) / beta`.

State the time unit — `mu`, `alpha` and `beta` are all rates and are
meaningless without it.

If the marked extension is used, say how excitation scales with the mark and
why that functional form.

## 4. Estimation

MLE via `scipy.optimize`. Report the log-likelihood, the optimiser and its
convergence status, starting values and sensitivity to them, and asymptotic
standard errors from the Hessian.

Recover known parameters from simulated data before fitting real data. An
estimator that cannot recover ground truth on its own simulator should not be
trusted on anything else, and this check is cheap.

## 5. Goodness of fit

Time rescaling: under a correctly specified intensity, the compensator-
transformed inter-arrival times are i.i.d. unit-rate exponential. Report the KS
statistic and p-value, and a QQ plot.

Not rejecting is weak evidence, not proof — an underpowered test fails to
reject almost anything. Report the number of residuals alongside the p-value.

Compare against a homogeneous Poisson null and at least one other alternative.
A Hawkes fit that does not beat Poisson on held-out likelihood has demonstrated
nothing.

## 6. Link to prices

Event-study regression of abnormal return on fitted diffusion speed, with the
benchmark model named (market model, FF3, or other) and the estimation window
stated. Control for the size of the news itself — otherwise the coefficient on
diffusion speed is absorbing surprise magnitude.

Report standard errors clustered appropriately. Overlapping event windows on
correlated tickers are not independent observations.

## 7. Causal versus correlational

This is the section a sharp reader turns to first, so it should be answered
before they ask.

Social volume and returns are both driven by the underlying news. Three
distinct claims are available — that diffusion *causes* price moves, that it
*correlates* with them, or that it *lags* them — and the write-up should say
which is being made and what evidence separates it from the other two.

Granger causality tests precedence, not causation, and precedence at
minute resolution is fragile to timestamp error. State the timestamp accuracy
of every source before leaning on lead–lag.

The scheduled-announcement subsample is the cleanest identification available
here: the announcement time is exogenous, so what happens after it is not
confounded by the decision of when to release news. Use it.

## 8. Limitations

Selection in which tickers get discussed, the survivorship of deleted posts,
regime dependence, and what would falsify the result.

## 9. Reproduction

Commands, seeds, data range, and package versions.
