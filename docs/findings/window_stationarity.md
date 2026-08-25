# The high branching ratios are tracking rate drift, not reflexivity

*2026-08-25. `scripts/window_stationarity.py`, 18 windows across the 9 slow-arm
markets, 200 parametric-bootstrap replications each. Raw output in
`results/diffusion/window_stationarity.json`.*

Study E established that a 2x rate step manufactures a branching ratio of 0.80
from data with no self-excitation, and that the time-rescaling diagnostics pass
on it. That was *sufficiency*. This measures whether the condition holds on the
real panel.

**It does. 12 of 18 windows vary more than their own fitted Hawkes can account
for, and the fitted branching ratio rises with the rate variation
(Spearman +0.73, p = 0.001).** The two windows with the largest rate swings are
the two with the largest branching ratios, and both land where Study E says a
step of that size should put them.

---

## The test, and why the obvious one is wrong

The naive approach — bin the window, ask whether counts look constant — tests
against Poisson. That is the wrong null, because **self-excitation produces
uneven bin counts by itself.** Calibrated against Poisson, every window rejects
and nothing is learned.

So the null here is *the fitted Hawkes itself*: fit the window, simulate 200
series from exactly those parameters over the same span, compute the same
dispersion statistic on each, and take the p-value as the fraction of
simulations at least as dispersed as the real window. A small p-value means the
window moves more than the model fitted to it can produce.

Statistic: index of dispersion of counts in 20 equal-**time** bins. Equal time,
not equal counts — the question is whether time is being filled evenly.

This is a parametric bootstrap and inherits the fit's assumptions. It cannot
prove stationarity, only fail to detect a departure.

## Results

| market | w | events | `n` | half-life | dispersion | null | p | halves | diag |
|---|---|---|---|---|---|---|---|---|---|
| SENATETX-26-D | 0 | 5,000 | 0.841 | 2423 | 182.9 | 30.1 | **0.005** | 2.14x | pass |
| SENATETX-26-D | 1 | 5,000 | 0.875 | 2580 | 78.8 | 43.6 | 0.080 | 2.61x | pass |
| SENATETX-26-D | 2 | 1,355 | 0.789 | 2325 | 10.3 | 12.1 | 0.662 | 1.05x | pass |
| KXGOVCA-26-XBEC | 0 | 5,000 | **0.937** | 806 | 370.5 | 109.6 | **0.010** | **5.62x** | REJ |
| KXGOVCA-26-XBEC | 1 | 5,000 | **0.904** | 2276 | 244.1 | 75.0 | **0.010** | **4.43x** | REJ |
| KXGOVCA-26-XBEC | 2 | 1,031 | 0.191 | 44 | 8.4 | 1.4 | **0.005** | 1.35x | REJ |
| SENATETX-26-R | 0 | 5,000 | 0.845 | 2288 | 312.8 | 31.6 | **0.005** | 2.42x | pass |
| SENATETX-26-R | 1 | 5,000 | 0.869 | 2303 | 80.2 | 37.5 | 0.060 | 2.39x | REJ |
| SENATETX-26-R | 2 | 901 | 0.832 | 1976 | 13.4 | 11.4 | 0.373 | 1.38x | pass |
| CONTROLH-2026-R | 0 | 5,000 | 0.041 | 0.05 | 13.7 | 1.0 | **0.005** | 1.13x | REJ |
| CONTROLH-2026-R | 1 | 4,356 | 0.769 | 2408 | 8.5 | 15.3 | 0.910 | 1.24x | REJ |
| KXGOVCA-26-SHIL | 0 | 5,000 | 0.906 | 1248 | 265.3 | 69.4 | **0.010** | 1.66x | pass |
| KXGOVCA-26-SHIL | 1 | 3,350 | 0.701 | 1989 | 88.0 | 9.5 | **0.005** | 1.92x | REJ |
| CONTROLH-2026-D | 0 | 5,000 | 0.633 | 2677 | 7.2 | 6.4 | 0.373 | 1.02x | REJ |
| CONTROLH-2026-D | 1 | 1,539 | 0.252 | 62 | 7.2 | 1.6 | **0.005** | 1.03x | REJ |
| …PSKY | 0 | 4,121 | 0.272 | 136 | 26.8 | 1.8 | **0.005** | 1.27x | REJ |
| …NONE | 0 | 2,361 | 0.209 | 17 | 23.5 | 1.5 | **0.005** | 1.19x | REJ |
| …NFLX | 0 | 2,244 | 0.151 | 11 | 20.2 | 1.3 | **0.005** | 1.58x | REJ |

## What it says

### 1. The branching ratio rises with the rate variation

| | Pearson | Spearman |
|---|---|---|
| all 18 windows | +0.533 (p = 0.023) | **+0.725 (p = 0.001)** |
| the 9 full 5,000-event windows | +0.536 (p = 0.137) | +0.717 (p = 0.030) |

Spearman is the one to read — the relationship is monotone but not linear, and
`n` is bounded above by 1, which flattens the top end and drags Pearson down.

### 2. The largest swings land where Study E predicts

| window | half-to-half rate ratio | fitted `n` |
|---|---|---|
| KXGOVCA-26-XBEC w0 | 5.62x | 0.937 |
| KXGOVCA-26-XBEC w1 | 4.43x | 0.904 |
| SENATETX-26-D w1 | 2.61x | 0.875 |
| SENATETX-26-R w0 | 2.42x | 0.845 |
| **Study E, simulated, zero true excitation** | **2.00x** | **0.799** |
| **Study E, simulated, zero true excitation** | **4.00x** | **0.942** |

The real windows sit on the curve traced by simulated data containing **no
self-excitation at all.** That is the whole finding.

### 3. Stationarity and the time-rescaling diagnostics are measuring different things

| | diagnostics reject | diagnostics pass |
|---|---|---|
| flagged non-stationary | 9 | 3 |
| not flagged | 3 | 3 |

Fisher exact **p = 0.344** — no association. Exactly what Study E predicted: a
rate departure inflates `n` while leaving residuals the diagnostics accept.
`SENATETX-26-R w0` is the clean example — `n` = 0.845, dispersion 10x its null,
p = 0.005, and **both diagnostics pass**.

This also means the 61–72% diagnostic rejection rate still has no explanation.
Not power-law misspecification (Study C, 10%), not preprocessing (flat across
the sweep), not rate drift (independent, per this table).

## What it does not say

**This is not proof that the high mode is an artifact.** Three limits:

1. **n = 18 windows**, of which 9 are full-length. The Spearman p of 0.001 is
   real but the sample is small and the windows within a market are not
   independent.
2. **Rate drift and self-excitation are not mutually exclusive.** A market can
   both cluster genuinely and drift. The test says the drift is larger than the
   fitted model explains, not that the excitation is zero.
3. **Six of eleven high-`n` windows are flagged, not eleven.** `SENATETX-26-D w1`
   (`n` = 0.875, 2.61x) has p = 0.080, and `SENATETX-26-R w1` (`n` = 0.869) has
   p = 0.060. Suggestive, not decisive, and honestly reported as such.

The defensible claim is: **a branching ratio from a flagged window cannot be
read as self-excitation.** Not: the panel shows no reflexivity.

## An anomaly worth chasing — and a correction

Three windows are strongly flagged while fitting `n` near zero:

| window | `n` | half-life | dispersion | null |
|---|---|---|---|---|
| CONTROLH-2026-R w0 | 0.041 | **0.045 s** | 13.7 | 1.0 |
| …NFLX w0 | 0.151 | 11 s | 20.2 | 1.3 |
| KXGOVCA-26-XBEC w2 | 0.191 | 44 s | 8.4 | 1.4 |

**An earlier draft of this document called these degenerate fits — optimiser
failures reporting `converged=True`. That was wrong, and checking it is what
found the real answer.**

Scoring `CONTROLH-2026-R w0` at hand-chosen slow kernels the multi-start grid
would never visit:

| kernel | log-likelihood |
|---|---|
| **as fitted** (half-life 0.045 s, `n` = 0.041) | **−37,785.76** |
| half-life 600 s, `n` = 0.6 | −37,912.84 |
| half-life 3,600 s, `n` = 0.9 | −37,914.54 |
| half-life 60 s, `n` = 0.9 | −42,298.53 |

The fit is not a local optimum. It beats every slow-kernel alternative by 130+
log-likelihood units. The optimiser is right; **the model is inadequate.**

### What the fit is actually capturing

`beta` = 15.2 means a kernel that decays in tens of milliseconds — after the
data has already been merged at 1 ms. The likelihood prefers spending its
excitation budget on **bursts within ~45 ms** rather than on anything at a
diffusion timescale.

That connects three findings that looked separate:

1. The sensitivity sweep found `n` jumping 0.466 → 0.723 when the merge window
   goes to 0.1 s, on 0.4% more prints. Sub-100 ms structure is doing enormous
   work.
2. This window's fit is dominated by exactly that structure.
3. The diagnostic rejection rate (61–72%) has resisted every explanation tried.

The unifying reading: **on some windows the exponential Hawkes is fitting
order-splitting microstructure, not information diffusion.** A single aggressive
order matching several resting orders emits prints milliseconds apart; 1 ms
merging removes the exact ties but not the tail. What survives is real
clustering at a timescale that has nothing to do with the phenomenon being
modelled, and the kernel — having only one timescale to spend — spends it there.

This makes the merge sweep above 10 ms the highest-value remaining diffusion
task, not a housekeeping detail.

## Actions

1. **Report the stationarity p-value beside every branching ratio**, in
   `fit_diffusion.py`, the way KS and Ljung-Box already are. A flagged window's
   `n` should be printed as uninterpretable rather than averaged in.
2. **Recompute the headline bimodality on unflagged windows only** and see
   whether the split survives.
3. **Extend the merge sweep above 10 ms** (0.01, 0.03, 0.1, 0.3, 1.0). This is
   now the highest-value diffusion task: the microstructure reading above
   predicts that `n` keeps moving as more of the sub-second tail is merged
   away, and that the diagnostic rejections fall with it. If both happen, the
   61-72% rejection rate is explained and so is the low mode.
4. **The diagnostic rejection rate remains unexplained** and is now the largest
   open question on the diffusion side.
