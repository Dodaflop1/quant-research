# Review of the power-law implementation

*2026-08-25. Review of `power_law.py` and `test_power_law.py` as first
delivered, against `docs/power_law_spec.md`.*

**Verdict: the implementation is sound and the validation is missing.** Merge
the code after the three fixes below, but do not report a single number from it
until Studies A–D exist.

---

## What is correct

Verified independently, not taken on trust:

- **The sum-of-exponentials approximation is accurate.** Compared against
  `phi(t) = n*eps*tau^eps/(t+tau)^(1+eps)` directly:

  | t | true | approx | ratio |
  |---|---|---|---|
  | 0 | 0.750000 | 0.750003 | 1.000 |
  | 0.1 | 0.590989 | 0.590989 | 1.000 |
  | 1 | 0.132583 | 0.132582 | 1.000 |
  | 10 | 0.00186887 | 0.00186887 | 1.000 |
  | 100 | 7.31573e-06 | 7.31575e-06 | 1.000 |

- **Exact-`n` renormalisation works**: `sum(c/beta) = 0.5000000000`.
- **The compensator algebra is right.** `n - sum_j (c_j/beta_j) exp(-beta_j(T-t_i))`
  is the correct per-event contribution.
- Log-space optimisation, identifiability-derived bounds, boundary solutions
  reported as non-convergence, multi-start — all consistent with the
  exponential estimator.

## Bugs to fix

### 1. The grid is rebuilt per call (correctness)

`_ensure_grid` derives `beta_min` from `T` and `beta_max` from the median gap of
whatever array it is handed, then caches on `self.beta is not None`. Since
`compensator()` constructs a fresh kernel each time, `compensator(t_end)` and
`compensator(t_start)` build **different grids**:

```
beta_max with (past < 300, upto=300):  21.8056
beta_max with (all times, upto=500):   22.1848
beta_min:                   0.000333 vs 0.000200
```

So their difference is not an integral over `[t_start, t_end]`. Measured
additivity error: `LL(0,T) - [LL(0,mid) + LL(mid,T)] = -0.00044`. Small on this
sample, larger when the subsets differ more — and held-out gains on the slow arm
run as low as 0.009 per event, so it is not safely negligible.

**Fix:** compute the grid once from the full series and the true `T`, then pass
it to every downstream call. The kernel is defined by `(n, tau, eps, grid)` — the
same parameters must never produce two different kernels.

### 2. `r` is a dead parameter (clarity)

`_compute_grid` builds the grid with `np.linspace`, whose log-spacing is
`(log beta_max - log beta_min)/(m-1)`, but the quadrature weight uses
`self._log_r = log(1.5)` regardless. The mismatch is a pure constant factor and
is absorbed by the renormalisation, so results are unaffected — but the code
reads as though `r` sets the spacing when it does not.

**Fix:** either derive the spacing from the actual grid
(`log_beta[1] - log_beta[0]`) and delete `r`, or build the grid geometrically
from `r` and derive `m`. Do not keep both.

### 3. `decay_states` uses the wrong window

`self._ensure_grid(times, times[-1] ...)` passes the last event time instead of
`T`. Currently masked by the cache, but it is a latent inconsistency.

## Missing validation — the substantive gap

### The one test that matters is absent

`test_kernel_normalization` asserts `sum(c/beta) == n`. That is **true by
construction** — the rescaling line forces it. The grid could be nonsense and
the test would pass. There is no test comparing the approximation to the actual
power law. Add the table above as a test.

### Studies A–D do not exist

`test_fit_power_law_basic` fits `np.random.uniform` data and asserts the
parameters are positive. That is not a recovery test; it asserts nothing about
correctness. All four studies from the spec are outstanding.

Two tests also use unseeded RNG (`test_fit_power_law_basic`,
`test_log_likelihood_on_interval`). A simulation study whose numbers cannot be
reproduced is an anecdote.

## Study A, run during this review

Truth `mu=0.30, n=0.50, tau=1.0, eps=1.5`, `T=4000`, ~2,350 events, 4 seeds,
simulated by Ogata thinning against the multi-exponential representation.

| | truth | median | bias |
|---|---|---|---|
| mu | 0.300 | 0.296 | −1.4% |
| **n** | **0.500** | **0.493** | **−1.4%** |
| tau | 1.000 | 0.716 | −28.4% |
| eps | 1.500 | 1.100 | −26.7% |

Per-seed `eps`: **0.98, 0.97, 1.22, 2.12.** A threefold spread.

### What this means, and it is important

**`n` is well identified; `tau` and `eps` individually are not.** They trade off
— a smaller `tau` with a smaller `eps` mimics a larger `tau` with a larger `eps`
over the observed lag range — while the combination governing the kernel
integral stays pinned.

Consequences:

- The branching ratio, which the whole fast/slow contrast rests on, is
  trustworthy at this sample size.
- **No claim about `eps` is supportable at ~2,400 events.** The spec asks for
  `eps` with a standard error and flags `eps -> 0` as the regime where `n -> 1`
  spuriously. Both require identifying `eps`, and this says we cannot yet.
- Before quoting `eps` anywhere, establish the sample size at which its spread
  becomes tolerable — or report it as unidentified and say so.

This was not known before the review and changes what the write-up can claim.

## Performance

~26 s per fit at 2,350 events with 27 starting points (3 x 3 x 3). For the slow
arm that is roughly 20 minutes for both kernels across 9 markets — fine. For the
fast arm at 20,000-event windows it will be hours. Consider trimming the start
grid once the likelihood surface is better understood, and report how many
starts actually win.

## Next instructions

1. Fix the three bugs above.
2. Add the approximation-quality test.
3. Implement Studies A–D from the spec, seeded throughout.
4. Report `n` recovery, the `eps` spread, and the noise floor from Study B.
5. **Study C is the experiment** — fit an exponential kernel to simulated
   power-law data and check whether it reproduces the bimodal branching ratio
   and diagnostic rejection seen on the real slow arm.

---

## Resolution — 2026-08-25

The first "fixed" resubmission was byte-identical to the original except for one
added comment: all four flagged lines were still present. Verified by grepping
the original for each. **Lesson: a model asked to fix code against prose review
will often return the same file. Hand it a literal diff, or the corrected file.**

`power_law.py` was rewritten instead. What changed:

1. **The grid is now a property of the data, not of the call.** `GridBounds` is
   computed once from the full series and the true `T`, then passed to every
   downstream call. Additivity error is now exactly `0.000e+00` (was `-4.4e-4`).
2. **`r` deleted.** The quadrature weight uses `log_beta[1] - log_beta[0]`, the
   actual grid step.
3. **`decay_states` takes `T`,** not `times[-1]`.
4. **`GridBounds.from_series` uses the 1st-percentile positive gap,** not the
   minimum, so one near-simultaneous pair cannot set `beta_max`.

### The default grid size in the spec was wrong

The spec said `M = 20–30`. Measured relative error of the sum-of-exponentials
approximation against the closed form:

| tau | eps | M=25 | M=40 | M=60 | M=100 |
|---|---|---|---|---|---|
| 1.0 | 3.0 | 1.39e-02 | 5.17e-05 | 1.31e-08 | 2.69e-14 |
| 10.0 | 0.5 | 9.53e-03 | 9.95e-03 | 1.04e-02 | 1.08e-02 |

Two distinct effects. For `eps >= 1` the error is grid **density** and more
points fix it — `_DEFAULT_M` raised to 40. For `eps < 1` the error is grid
**span** and more points do not help: `eps < 1` has infinite mean lag and
`beta_min = 1/(10T)` is an identifiability limit set by the observation window,
not a defect. Tested separately at a 2% tolerance, with the branching ratio
still asserted exact.

### Recovery after the fixes

| | truth | before | after |
|---|---|---|---|
| n | 0.500 | −1.4% | **+0.1%** |
| tau | 1.000 | −28.4% | +1.8% |
| eps | 1.500 | −26.7% | +9.0% |

Standard errors: `se(n) ~ 7%`, `se(eps) ~ 22–54%`. The conclusion of the review
stands and is now enforced in the type: `PowerLawParameters.eps_is_identified`
is False without a tight standard error, and `describe()` prints
`[eps NOT identified]`. **`n` is reportable at ~2,400 events; `eps` is not.**

Suite: 283 passed (29 new). **Studies B, C, D remain outstanding.**
