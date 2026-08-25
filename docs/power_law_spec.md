# Power-law kernel — specification

*The next piece of work, and the critical path for Project 2. Written
2026-08-25 with the exponential-kernel results in hand.*

---

## 1. Why — the evidence that forces this

Nine markets, slow arm, exponential kernel, after aggregating prints into orders
and deseasonalising:

| markets | mean n | half-life range | diagnostics |
|---|---|---|---|
| 4 | 0.88 | 975 – 6,221 s | 5 of 12 windows pass |
| 5 | 0.23 | 11 – 1,849 s | 0 of 6 windows pass |

Three observations, all pointing the same way:

1. **The branching ratio is bimodal and tracks half-life.** Nothing between 0.32
   and 0.83. A single exponential can match the head of an excitation kernel or
   its tail, not both, so the optimiser picks a local optimum according to which
   timescale dominates. Two answers to the same question from the same model.
2. **Diagnostics reject 13 of 18 windows.** For scale, the seasonality study
   found these same tests catching a *known* misspecification only 12% of the
   time. A 72% rejection rate means the failure is severe, not marginal.
3. **Deseasonalising changes nothing** — median per-market shrink +0.004. The
   non-stationarity is not periodic, so the baseline is not the problem.

Meanwhile **9 of 9 markets beat Poisson on held-out likelihood**, so the
clustering is real. Something is there; the exponential cannot describe it.

Long memory is the standard explanation, and it is exactly what the
Hardiman–Bercot–Bouchaud critique of Filimonov–Sornette turns on. Establishing
it *on our own data* rather than citing it is the contribution.

## 2. The kernel

Use the Omori form standard in this literature:

```
phi(t) = n * eps * tau^eps / (t + tau)^(1 + eps),     t >= 0
```

with `n > 0`, `tau > 0` (a short-lag cutoff), `eps > 0` (tail index). The
normalisation is chosen so that

```
integral_0^inf phi(s) ds = n
```

exactly — so **`n` remains the branching ratio and stays comparable to every
number already reported.** Verify this in a unit test; it is the one property
the whole comparison rests on.

Conditional intensity:

```
lambda(t) = mu + sum_{t_i < t} phi(t - t_i)
```

Report `eps` with its standard error. `eps -> 0` is the regime where
Hardiman–Bouchaud argue `n` is driven to 1 spuriously, so a fitted `n` near 1
with a small `eps` is a warning, not a result.

## 3. The computational problem, and the fix

The exponential kernel's whole appeal is the recursion

```
A_i = exp(-beta * (t_i - t_{i-1})) * (1 + A_{i-1})
```

which makes the likelihood O(n). A power law is not memoryless, so written
directly the likelihood is **O(n²)** — hopeless at 3.56M events.

**Do not truncate the kernel.** Truncation discards exactly the long tail the
experiment is about.

**Approximate the power law by a sum of exponentials.** Use the Gamma integral
representation:

```
(t + tau)^(-(1+eps)) = 1/Gamma(1+eps) * integral_0^inf u^eps * exp(-u*(t+tau)) du
```

Discretise `u` on a geometric grid `u_j = u_0 * r^j`, `j = 0..M-1`. Because
`du = u * d(log u)`, geometric spacing gives

```
w_j = (1/Gamma(1+eps)) * u_j^(eps+1) * exp(-u_j * tau) * log(r)
```

so that

```
phi(t) ~= sum_j c_j * exp(-beta_j * t),    beta_j = u_j,
                                            c_j = n * eps * tau^eps * w_j
```

**Then rescale all `c_j` by a single constant so that `sum_j c_j / beta_j == n`
exactly.** This removes discretisation error from the branching ratio, which is
the quantity being reported. Assert it in a test.

Grid: **`M = 40`**, `beta` spanning `1/(10T)` up to `10/q01_gap`, where `q01_gap`
is the 1st percentile of the *positive* inter-arrival gaps.

> **Corrected 2026-08-25.** This paragraph originally said `M = 20–30` and used
> the *median* gap. Both were wrong and the implementation faithfully reproduced
> them. Measured relative error of the approximation against the closed form:
>
> | tau | eps | M=25 | M=40 | M=60 | M=100 |
> |---|---|---|---|---|---|
> | 1.0 | 3.0 | 1.39e-02 | 5.17e-05 | 1.31e-08 | 2.69e-14 |
> | 10.0 | 0.5 | 9.53e-03 | 9.95e-03 | 1.04e-02 | 1.08e-02 |
>
> For `eps >= 1` the error is grid *density*, and `M = 40` fixes it. For
> `eps < 1` it is grid *span*: infinite mean lag means a finite grid truncates
> the tail, more points do not help, and `beta_min = 1/(10T)` is an
> identifiability limit set by the observation window rather than a defect.
> Report an `eps < 1` fit with that caveat. The branching ratio is unaffected
> either way — it is exact by construction.
>
> The median gap was the wrong ceiling for the same reason it was wrong in the
> exponential estimator: a single near-simultaneous pair drags `beta_max` up by
> orders of magnitude. Use a low quantile of the positive gaps.

The grid is a property of **the series**, not of the call. Compute it once from
the full data and the true `T`, then pass it to every downstream call. If
`compensator(t_end)` and `compensator(t_start)` build different grids their
difference is not an integral over the window — that bug cost 4e-4 of additivity
against held-out gains that run as low as 0.009 per event.

Check convergence by re-fitting with `M` doubled — if `n` moves materially, the
grid is too coarse.

### Likelihood with M components

Every component keeps its own recursion, so:

```
lambda(t_i) = mu + sum_j c_j * A_j(i)
A_j(i)      = exp(-beta_j * dt_i) * (1 + A_j(i-1))
```

Cost O(n·M) — at M = 40 that is 40× the exponential fit, which is acceptable.

Compensator:

```
Lambda(T) = mu*T + sum_i sum_j (c_j / beta_j) * (1 - exp(-beta_j * (T - t_i)))
```

Fitted parameters are `(mu, n, tau, eps)`; `c_j` and `beta_j` are derived. Fit in
log space with identifiability-derived bounds, exactly as the exponential does.

**Reuse everything else.** `diagnostics.check_fit`, `baseline.deseasonalise`,
`preprocess.aggregate_simultaneous`, and the held-out scoring all take a
compensator and an intensity — they do not care which kernel produced them.
Refactor them to accept a kernel object rather than `(mu, alpha, beta)`.

## 4. Validation sequence — in this order, before any real data

Follow the pattern already established in `scripts/hawkes_recovery.py`. The
estimator must be characterised before it is trusted.

**Study A — recovery.** Simulate a power-law Hawkes by Ogata thinning (the
multi-exponential form makes the intensity easy to evaluate), then recover
`(mu, n, tau, eps)`. Report bias and 95% CI coverage across replications, not
just point estimates. The exponential estimator's coverage on `alpha` came out
at 85%, not 95% — expect worse here with four parameters.

**Study B — negative control.** Poisson data must give `n` near zero. Establish
the noise floor for this kernel; the exponential's is 0.19 and there is no reason
to assume they match.

**Study C — the key experiment. Fit an EXPONENTIAL kernel to simulated
POWER-LAW data.**

This is the one that matters. If exponential fits of power-law data reproduce
what we see on the real slow arm — **bimodal `n` tracking half-life, and
systematic diagnostic rejection** — then the real data's misspecification is
demonstrated rather than asserted. Sweep `eps` and check whether the bimodality
appears and where.

A positive result here is publishable reasoning on its own, independent of what
the power-law fit then returns on real data.

**Study D — the reverse.** Fit the power-law kernel to exponential data. It
should recover with large `eps` (a power law with a heavy index approaches an
exponential) and should not invent long memory. Guards against the new kernel
simply fitting everything.

## 5. Then the real data

Run `fit_diffusion.py` with both kernels side by side and report:

| | exponential | power law |
|---|---|---|
| branching ratio | | |
| tail index `eps` | — | |
| diagnostics passed | 5 / 18 | |
| held-out LL vs Poisson | 9 / 9 beat | |
| held-out LL vs exponential | — | |

### Success criteria, stated before running

- **Primary:** the power law passes the diagnostics on markets where the
  exponential is rejected.
- **Secondary:** the bimodality collapses — one coherent `n` per market rather
  than two attractors.
- **Tertiary:** power law beats exponential on held-out likelihood by more than
  its extra parameter buys (compare per-event, and report AIC).

**A negative result here is still a result.** If the power law is also rejected,
the honest finding is that neither parametric kernel describes Kalshi order flow
— which is worth reporting and points at marks, or a non-parametric kernel
estimate, as the next step.

## 6. Then the fast arm

`data_fast/raw/`, 3.56M events over 9h13m, exchange-wide. Use
`--period 3600 --window-events 20000`. Aggregate prints first — the tie problem
will be worse there, not better.

**The slow/fast contrast is Project 2's thesis:** if the branching ratio is
near-critical in the fast arm and modest in the slow arm, on the same exchange
with the same estimator and the same diagnostics, that is direct evidence about
what published near-unity estimates are measuring.

## 7. Do not skip

- Aggregate prints into orders first. Raw prints give n=0.664 against a truth of
  0.500 **and report convergence** (finding 9).
- Report naive and deseasonalised both ways, even though it changed nothing on
  the slow arm. It changes a great deal when seasonality is present.
- Report the number of residuals with every p-value. An underpowered test fails
  to reject almost anything.
- Keep `n` invariant under the time change and assert it.
- Boundary solutions are non-convergence. Do not quote their standard errors.

## References

- Filimonov & Sornette (2012), *Quantifying reflexivity in financial markets*
- Hardiman, Bercot & Bouchaud (2013), *Critical reflexivity in financial
  markets: a Hawkes process analysis*
- Bacry, Mastromatteo & Muzy (2015), *Hawkes processes in finance*
