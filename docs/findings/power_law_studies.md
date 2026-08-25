# Power-law validation studies B, C and D

*2026-08-25. Produced by `scripts/power_law_studies.py`, seeded from
`BASE_SEED = 20260825`. Rerunning the script reproduces every number here.*

**Headline: Study C returns a negative, and it is the useful kind.** Fitting an
exponential kernel to power-law data does **not** reproduce what we see on the
real slow arm. The bimodal branching ratio and the 72% diagnostic rejection rate
are therefore *not* explained by this misspecification, and need another cause.

---

## Study B — negative control

12 replications of a homogeneous Poisson process, `rate = 0.6`, `T = 4000`
(~2,400 events), each fitted with the power-law kernel.

| | value |
|---|---|
| median `n` | **0.016** |
| 10–90 band | 0.000 – 0.062 |
| **noise floor (95th pct)** | **0.141** |
| exponential kernel's floor, for comparison | 0.19 |
| fits landing on a `tau`/`eps` bound | 10 / 12 |

The power-law kernel's noise floor is **0.141**, below the exponential kernel's
0.19. The real slow arm's lower mode sits at `n ≈ 0.23` — above both floors, but
by less than a factor of two on the exponential floor. That is thin.

### On the 83% boundary rate

Ten of twelve fits parked on a bound in `tau` or `eps`. This is the estimator
being correct, not failing: Poisson data has no excitation, so there is nothing
to identify the *shape* of a kernel whose amplitude is ~0. `n` is still
estimated fine — hence recording it regardless of the convergence flag.

The first version of this study discarded estimates from non-converged fits, and
so computed the floor from the two runs where noise happened to look structured.
That gave 0.060 from `count: 2`. The corrected figure over all twelve is 0.141 —
**more than double**, and in the direction that matters. A negative control that
throws away its failures is not a control.

## Study C — the key experiment

Simulate power-law data (`mu = 0.30`, `n = 0.50`, `tau = 1.0`, `T = 4000`),
sweep `eps`, fit the **wrong** (exponential) kernel. 12 replications per `eps`.

| `eps` | fitted `n` [p10, p90] | half-life [p10, p90] | diagnostics rejected | bimodal? |
|---|---|---|---|---|
| 0.5 | 0.334 [0.310, 0.350] | 1.15 [0.90, 1.60] | **6 / 12** | no (ΔBIC −6.1) |
| 1.0 | 0.405 [0.390, 0.455] | 0.68 [0.64, 0.76] | 0 / 12 | no (ΔBIC −5.4) |
| 1.5 | 0.425 [0.399, 0.476] | 0.48 [0.42, 0.53] | 0 / 12 | no (ΔBIC −1.4) |
| 2.5 | 0.475 [0.441, 0.494] | 0.30 [0.26, 0.33] | 0 / 12 | no (ΔBIC −1.4) |
| 4.0 | 0.481 [0.465, 0.503] | 0.19 [0.18, 0.20] | 0 / 12 | no (ΔBIC −0.9) |

Truth is `n = 0.50` in every row.

**(a) Is fitted `n` bimodal? No.** Not within any single `eps` — every ΔBIC is
negative, meaning one Gaussian component is preferred over two at every point in
the sweep. Nor does the sweep as a whole produce a split: `n` moves smoothly
from 0.334 to 0.481, a total range of **0.15**. The real split is 0.23 vs 0.88,
a range of **0.65**. Misspecification of this kind cannot manufacture a spread
four times smaller than the one observed, and it moves `n` *downward* from the
truth — never up toward 0.88.

**(b) Does high `n` travel with a long half-life? The opposite.** Across the
sweep, a heavier tail (lower `eps`) gives a *lower* fitted `n` and a *longer*
half-life: pooled correlation **−0.80**. The real data shows the reverse — the
`n ≈ 0.88` markets are the ones with half-lives of 975–6221 s. The sign is
wrong, so this mechanism does not explain the real pattern.

> **A trap worth recording.** The *within*-`eps` correlation is **+0.48**
> (range −0.19 to +0.79) — the opposite sign to the pooled **−0.80**. A textbook
> Simpson's paradox: sweeping `eps` moves `n` and the half-life in opposite
> directions, and pooling lets that dominate the within-group relationship.
> Either number can be quoted to support either conclusion. Which one is the
> real-data analogue depends on whether the nine markets share a tail index or
> differ in it — and we do not know that yet, so **both are reported and neither
> is used alone.**

**(c) Diagnostic rejection: 6/60 = 10%, against 13/18 = 72% on real data.** All
six rejections are at `eps = 0.5`. So misspecification *is* detectable by the
time-rescaling diagnostics, but only for a tail heavy enough to have infinite
mean lag, and even then only half the time. It does not produce anything like
the real rejection rate.

### What this means

Three independent signatures of the real slow arm — bimodality, the sign of the
`n`/half-life relationship, and the rejection rate — and exponential-on-power-law
reproduces **none** of them. The misspecification hypothesis is not supported.

Candidates that remain, in the order they should be tested:

1. **Residual seasonality.** Deseasonalising moved the median per-market `n` by
   only +0.004, which is suspiciously small given that seasonality alone can
   fabricate `n = 0.79–0.95` on zero-self-excitation data. Either the profile is
   capturing the seasonality properly, or it is not capturing it at all.
2. **Remaining timestamp ties.** Merging cut 13.6–70.1% of prints. A 1 ms
   tolerance may be leaving structure behind on the markets at the high end.
3. **Genuine heterogeneity in `n` across markets.** The null this study cannot
   address, because `n` was held at 0.50 throughout.
4. **Non-stationarity within windows.** A market whose rate steps mid-window
   will fail Ljung-Box without any misspecification of the kernel.

### Scope limit — state this in the write-up

`n` was fixed at 0.50 for every replication. The study therefore answers
"does this misspecification *manufacture* a spread in `n`?" (no) and **not**
"do the markets genuinely differ in `n`?" (untested). The finding is *not this
artifact*, which is weaker than *the bimodality is real*.

The half-life comparison is also scale-dependent: `tau = 1.0` sets the time
scale in simulation, so the simulated half-lives of 0.19–1.15 s are not directly
comparable in magnitude to the real 11–6221 s. Only the **sign** of the
relationship carries across, and that is what is used above.

## Study D — the reverse

12 replications of exponential-kernel data (`mu = 0.30`, `alpha = 0.5`,
`beta = 1.0`, so true `n = 0.5`), fitted with the power-law kernel.

| | value |
|---|---|
| fitted `n` median | **0.507** (truth 0.500, bias 0.007) |
| 10–90 band | 0.477 – 0.545 |
| fitted `eps` median | **5.00 — censored at the upper bound** |
| `eps` at the ceiling | 11 / 12 fits |
| `eps` identified (`se < 0.25·eps`) | **0 / 12** |

**The power-law kernel does not invent long memory.** Given exponential data it
recovers `n` to within 0.007 and pushes `eps` to its ceiling, which is the
model's way of saying "this tail is not heavy" — a power law with a large index
approaches an exponential. The kernel does not fit everything, which is what
this study exists to check.

Two caveats, both of which belong in any sentence that quotes `eps`:

- The `eps` estimate is **censored**, not estimated: 11 of 12 fits sit on the
  bound, so the median of 5.00 is the bound and not a measurement. The correct
  statement is "`eps` ran to its ceiling in 11/12 fits", never "`eps` = 5.0".
- `eps` was identified in **0 of 12** fits by the repo's own criterion. This
  matches Study A (se 22–54% at ~2,400 events) and is consistent with the
  standing conclusion: **`n` is reportable at this sample size, `eps` is not.**

---

## Reproducing

```bash
python scripts/power_law_studies.py --studies B C D --reps 12 \
    --out results/diffusion/power_law_studies.json \
    --report results/diffusion/power_law_studies.md
```

Roughly 40 minutes: Studies B and D fit the power-law kernel (~25 s each, ~35 s
with standard errors), Study C fits the much cheaper exponential kernel 60 times.
