# What the slow arm actually is

*2026-08-25. Combines `scripts/sensitivity_sweep.py` (12 cells over real data)
and `scripts/nonstationarity_study.py` (Study E, 24 replications per cell).
Raw output in `results/diffusion/sensitivity_sweep.json` and
`results/diffusion/nonstationarity_study.json`.*

**A rate step of 2x, on data with no self-excitation whatsoever, produces a
fitted branching ratio of 0.80 with a half-life of 1,101 s — and the
time-rescaling diagnostics pass 24 times out of 24.**

The real slow arm's high mode is `n ~ 0.88` with half-lives of 975–6,221 s. It
is inside the range that within-window rate variation manufactures out of
nothing. **The high branching ratios cannot be presented as evidence of
reflexivity until the arrival rate is shown to be stable inside each fitting
window.** That check does not currently exist.

---

## Study E — a rate step is sufficient to fabricate the high mode

One-day windows, ~2,500 events, total event count held fixed across step sizes
so that a larger step does not also mean more data. Fitted with the exponential
kernel. `E1` has **zero** self-excitation by construction.

### E1 — pure Poisson with a step (truth `n` = 0)

| step | fitted `n` [p10, p90] | half-life (s) | diagnostics rejected |
|---|---|---|---|
| 1x | 0.034 [0.000, 0.151] | 16 | 4/21 |
| **2x** | **0.799** [0.776, 0.814] | **1,101** [928, 1588] | **0/24** |
| 4x | 0.942 [0.933, 0.946] | 811 [764, 1026] | 2/24 |
| 8x | 0.975 [0.972, 0.979] | 688 [629, 761] | 2/24 |
| 16x | 0.989 [0.984, 0.993] | 614 [516, 652] | 1/24 |

With no step the estimator is clean — 0.034, below the measured noise floor of
0.19. One doubling of the rate halfway through the window takes it to 0.80.

### E2 — genuine Hawkes with a step (truth `n` = 0.3)

| step | fitted `n` | half-life (s) | rejected |
|---|---|---|---|
| 1x | 0.294 [0.100, 0.395] | 339 | 1/24 |
| 2x | 0.829 [0.796, 0.859] | 980 | 3/24 |
| 16x | 0.988 [0.983, 0.993] | 549 | 2/24 |

A real branching ratio of 0.3 recovers correctly with no step, and is inflated
to 0.83 by a doubling. The construction simulates the two halves independently,
which *understates* cross-boundary excitation — so these inflations are a lower
bound.

### Why the mechanism works

A Hawkes fit assumes a constant baseline. Given a series whose rate steps up
partway through, it has two ways to account for the extra events: raise `mu`, or
attribute them to self-excitation. Self-excitation wins, because a constant `mu`
cannot produce a *sustained* elevation but a slow-decaying kernel can. The fitted
half-life then lands in the hundreds-to-thousands of seconds — it is standing in
for a regime lasting half the window, not for a burst.

### The part that matters most: the diagnostics do not catch it

**0 of 24 rejections at the 2x step, where `n` is entirely fabricated.**

This is not a defect in the diagnostics; it is what they test. Time-rescaling
checks whether the *fitted intensity path* explains the arrival times. A
slow kernel tracking a rate step does explain them, so the rescaled residuals
are iid Exp(1) and both tests pass. **Time-rescaling cannot distinguish
self-excitation from a time-varying baseline** — the two produce the same
intensity path and differ only in mechanism.

Consequence for the write-up, stated plainly: **the 5 of 18 windows that pass
diagnostics are not thereby validated.** A passing diagnostic is consistent with
a branching ratio manufactured entirely by non-stationarity. Every previous
statement that treated a diagnostic pass as support for a branching ratio needs
correcting.

## Sensitivity sweep — preprocessing is not the explanation, but one constant is not safe

12 cells, `--merge-window` x `--period`, 9 markets, everything else at defaults.

### What is stable

- **9/9 markets beat the held-out Poisson benchmark in every cell.** The series
  are not constant-rate. That result survives every preprocessing choice.
- **Diagnostics barely move: 5/18 to 7/18 passing** (28%–39%). Rejection runs
  61%–72% no matter what. Preprocessing does not explain the diagnostic
  failures, and neither does power-law misspecification (Study C: 10%). **That
  remains unexplained** and is the open question.
- **Seasonal period barely moves the per-market result.** Median per-market
  shrink is +0.000 at 900 s, −0.0002 at 3,600 s, +0.004 at 86,400 s.

### What is not stable

**The merge window is load-bearing at 0.1 s.** Naive median `n` jumps from
**0.466 to 0.723** — and events surviving the merge falls only from 69.6% to
69.2%. Merging an extra **0.4%** of prints moves the branching ratio by
**0.26**.

| merge window | naive `n` | events surviving | diagnostics |
|---|---|---|---|
| 0.0001 s | 0.466 | 69.6% | 5/18 |
| 0.001 s (current) | 0.466 | 69.6% | 5/18 |
| 0.01 s | 0.466 | 69.5% | 5/18 |
| **0.1 s** | **0.723** | 69.2% | 7/18 |

0.1 ms through 10 ms are identical to three decimals, because ~30% of prints are
*exact* ties and any positive tolerance catches all of them. The interesting
region is above 10 ms, and it was never explored. A result that moves by 0.26 on
0.4% of the data is being driven by a handful of near-simultaneous prints.

### A trap in reading this table

The naive and deseasonalised rows are **medians across markets**. Their
difference is a difference of medians, and at 86,400 s that reads as
0.466 → 0.321, a shift of 0.145. The **median per-market shrink is +0.004**.
These disagree by a factor of 36 because the two medians come from different
markets — the same error `fit_diffusion.py` already carries a comment about. The
shrink row is the honest number; do not quote the difference of the other two.

---

## Extended merge sweep — the artifact is a band, not a slope

*Added after the second sweep, `MERGE_WINDOWS = [0.01, 0.03, 0.1, 0.3, 1.0]`.*

The first sweep found `n` jumping 0.466 → 0.723 at a 0.1 s merge tolerance and
could not say whether that was the start of a slope or a step, because
everything above 10 ms was unexplored. **It is a step.**

| merge | naive `n` [p10, p90] | events surviving | diagnostics passing |
|---|---|---|---|
| 0.01 s | 0.466 [0.208, 0.867] | 69.5% | 5/18 |
| 0.03 s | 0.466 [0.207, 0.866] | 69.5% | 6/18 |
| **0.1 s** | **0.723** [0.203, 0.868] | 69.2% | 7/18 |
| 0.3 s | 0.725 [0.198, 0.870] | 69.0% | 7/18 |
| 1.0 s | 0.726 [0.195, 0.868] | 68.7% | 7/18 |

Per step:

| step | Δ`n` | Δevents | Δ`n` per 1% of events removed |
|---|---|---|---|
| 0.01 → 0.03 | +0.000 | 0.00% | — |
| **0.03 → 0.1** | **+0.257** | **0.30%** | **0.86** |
| 0.1 → 0.3 | +0.002 | 0.20% | 0.01 |
| 0.3 → 1.0 | +0.001 | 0.30% | 0.00 |

**99% of the total move happens in one step, between 30 ms and 100 ms, on 0.30%
of the events.** Above 100 ms the branching ratio is flat to three decimals
while merging continues to remove events at the same rate. This is a discrete
population of near-simultaneous prints separated by tens of milliseconds — not a
continuum of clustering that merging gradually erodes.

### The direction is the surprise

Merging removes events, and the naive reading is that removing artificial
clustering should *lower* the branching ratio. It rises, by 0.26.

The explanation is the one `CONTROLH-2026-R w0` already showed: with the 30–100
ms prints intact, the likelihood spends its entire excitation budget on them —
`beta` = 15.2, a 45 ms half-life on a window spanning weeks, `n` = 0.041.
**The microstructure was suppressing the branching ratio by capturing the
kernel, not inflating it.** Merge those prints away and the single available
timescale is freed to fit structure at the diffusion scale, where `n` is higher.

A one-timescale kernel can describe the millisecond band or the hour band, not
both, and the likelihood prefers the millisecond band whenever it is present.
That is an argument for a multi-scale kernel, and it is the one place the
power-law work still has something to offer — Study C ruled it out as an
explanation for the *bimodality*, not as a better description of the data.

### What did not happen, and it matters

**The diagnostic rejections did not follow.** 13/18 → 12/18 → 11/18, then flat
at 11/18 for the last three cells. Removing the artifact that moves `n` by 0.26
buys two windows. The 61–72% rejection rate is not microstructure either, and
after four attempts — power-law misspecification (10%), preprocessing (flat),
rate drift (independent), microstructure (buys 2 of 13) — **it remains the
largest unexplained thing in this project.**

**The bimodality did not move.** Across the entire grid the 10th percentile sits
at 0.195–0.208 and the 90th at 0.866–0.870. Only the median travels. With nine
markets the median is the fifth, so the jump is a re-ordering of the middle
while both modes stay exactly where they were.

**That is the useful negative.** The 0.23 / 0.88 split is invariant to a merge
tolerance swept over two orders of magnitude and to a seasonal period swept over
two. It is not a preprocessing artifact. Combined with Study C ruling out kernel
misspecification and the stationarity result explaining the *high* mode as
drift, the low mode is now the open question — and the microstructure-captured
fits (`n` = 0.041, 0.151, 0.191, all with sub-minute half-lives) are the obvious
candidates for what it is made of.

### Consequence for the reported numbers

The current default is `--merge-window 0.001`, which sits in the flat region
*below* the step. Every branching ratio in the write-up is therefore computed
with the 30–100 ms prints still present, and is depressed by roughly 0.26 at the
median relative to a fit that removes them. **Either move the default to 0.1 s
and re-run everything, or report both and say why.** The second is more honest
and costs one extra column.

## Where this leaves the project

| candidate explanation for the slow arm | status |
|---|---|
| power-law misspecification | **ruled out** (Study C: no bimodality, wrong sign, 10% rejection) |
| residual seasonality | **ruled out** as the driver (per-market shrink ≤ 0.005 at every period) |
| remaining timestamp ties | **partly implicated** — 0.26 of `n` rides on 0.4% of prints |
| **within-window non-stationarity** | **sufficient to produce the high mode** (Study E) |
| heterogeneity in true `n` across markets | untested |
| whatever drives 61–72% diagnostic rejection | **unexplained** |

### What Study E does and does not establish

It establishes **sufficiency**: a 2x step manufactures `n ~ 0.8` from nothing. It
does **not** establish that the real windows contain such steps — that has not
been measured. The honest claim is "the high mode is not attributable to
self-excitation without a stationarity check", not "the high mode is an
artifact".

## Next, in order

1. **Measure the within-window rate variation on the real data.** Split each
   fitting window in halves or thirds and report the ratio of arrival rates.
   Until that number exists, no branching ratio from this panel is
   interpretable. This is the single highest-value thing outstanding.
2. **Add a stationarity test to `fit_diffusion.py`** and report it beside every
   `n`, the way KS and Ljung-Box already are. A window that fails it should have
   its branching ratio reported as uninterpretable rather than quietly averaged
   in.
3. **Extend the merge sweep above 10 ms** — 0.01, 0.03, 0.1, 0.3, 1.0 — and find
   where the 0.26 jump happens and what is in those prints.
4. **Correct the existing methodology text** wherever a diagnostic pass is
   treated as validating a branching ratio.
