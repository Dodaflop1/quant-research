# Quant research project — complete context bundle

You are picking up an in-progress quantitative research project. **You have no
prior context and no access to the repository**, so everything you need is
inlined below: the briefing, the specification for the next task, and the source
files you will need to read or extend.

Work through it in this order:

1. **BRIEFING** — what the project is, what is established, what the traps are
2. **THE TASK** — the next piece of work, fully specified
3. **SOURCE** — the existing implementation you will mirror and reuse

The user has the full repository locally and can paste any other file on
request. Ask for one rather than guessing at its contents.

Two conventions this project is held to, because they have already caught
several bugs that a single well-behaved run hid completely:

- **Simulate before fitting.** Validate every estimator against data whose
  answer is known before it touches real data.
- **Every number must be traceable to code or data.** Nothing asserted.

---



==============================================================================
# BRIEFING

`HANDOFF.md`
==============================================================================

# Project handoff — read this first

*Written 2026-08-25, day 5 of a 6-week plan. Self-contained: assumes you know
nothing about this project.*

---

## What this is

Two portfolio projects intended to get **Luca Bonnici** (Dartmouth
undergraduate, no PhD) hired as a quantitative researcher. The audience is a
quant research panel who will read the code and the write-ups and ask hard
questions about assumptions, identification, and calibration.

1. **Kalshi mispricing engine** — structural arbitrage in event contracts.
   Demonstrates trading-systems competence.
2. **Information diffusion / reflexivity** — Hawkes point-process modelling of
   trade arrivals. Demonstrates research competence.

**Design principle throughout: a negative result honestly established beats a
curve-fit backtest.** Both projects currently lead with negative or constraining
findings, deliberately.

## Where the work lives

| | |
|---|---|
| Repo | `C:\Users\lucae\Downloads\quant-research` (Windows), pushed to GitHub |
| Server | Oracle Cloud `159.54.168.0`, Ubuntu 24.04, VM.Standard.E2.1.Micro |
| SSH | `ssh -i $env:USERPROFILE\.ssh\oracle_key ubuntu@159.54.168.0` |
| Server app dir | `/opt/quant-research`, service account `collector` |
| Tests | 254 passing (`pytest tests/unit`) |

**Run repo commands on the server as the owner:**
`sudo -u collector .venv/bin/python ...` — files are chowned to `collector`.

**The server venv has only 4 packages** (requests, dotenv, cryptography,
pydantic). No numpy/scipy. **Analysis happens on Windows**, where the full stack
is installed. Pull data down with `scp`; don't install numpy on the collector.

---

## Current state

### Collection (running, unattended)

- 150 pinned Kalshi markets, 60-second full-depth order book snapshots
- Since 2026-08-25 00:53 UTC, under systemd with `Restart=always`
- Plus a 5-minute **watchdog timer** that restarts on *stale data*, not on
  process death — systemd cannot see an alive-but-hung process, and that is the
  failure this project actually hit
- Verified clean: 150/150 markets full-span, zero outages, median and p95
  sampling 60s

### Data on hand

| | |
|---|---|
| Server order books | 2026-08-25 onward, 150 markets, ~240 MB/day |
| **Laptop order books** | 08-23 → 08-24, **different universe — do not concatenate** |
| Slow-arm trades | 151,477 over 89 days, 148 markets |
| Fast-arm trades | 3.56M in 9h13m, exchange-wide, 1.08 GB, in `data_fast/raw/` |

### Written

- `docs/kalshi_methodology.md` (331 lines)
- `docs/diffusion_methodology.md` (347 lines)
- `docs/findings/` — detailed result notes
- `docs/power_law_spec.md` — **the next piece of work, fully specified**
- `TASKS.md` — checklist
- `deploy/README.md` — server setup, including why Dartmouth's HPC cluster is
  the wrong shape for this

---

## Findings — established, numbers final

### Project 1 (Kalshi)

1. **The fee ceiling.** The taker fee is
   `ceil(multiplier × 0.07 × contracts × P × (1−P))` rounded up **to a whole
   cent per leg**, against a fixed 100¢ payout. So an N-leg basket owes **at
   least N cents**. Past ~10 legs no dislocation of any size can clear it. This
   is arithmetic, not empirics, and it is the strongest result in Project 1.
2. **Direction asymmetry.** Shorting a basket needs only *mutual exclusivity*,
   which Kalshi flags. Going long needs *collective exhaustiveness*, which it
   does not certify, titles cannot reveal, and prices cannot settle (a low ask
   sum is either an arbitrage or a leaky family). **Only the short side is
   identifiable.**
3. **Long direction closed at the touch.** Every family sampled: ask sum
   100.4–109.2¢.
4. **Large-field overround is structural.** Minimum tick props up every
   longshot; 243¢ across 293 families in one run. Not bad data.
5. **Capacity is not volume.** One family: 368,438 volume, one contract at the
   ask. Flagged families show capacities of 0–94 contracts, mostly single digits.

### Project 2 (Diffusion)

6. **Seasonality fabricates reflexivity.** On simulated arrivals with **zero**
   self-excitation (inhomogeneous Poisson, sinusoidal rate), a constant-baseline
   Hawkes fit reports a branching ratio of **0.79–0.95**, and the residual
   diagnostics reject it only **12%** of the time. Published order-flow branching
   ratios sit at 0.8–0.9.
7. **Noise floor 0.19.** The 95th-percentile branching ratio on pure Poisson
   data. Any real estimate must clear it.
8. **The seasonal time change works and is specific.** Spurious 0.891 → 0.020;
   genuine 0.484 → 0.481. Cost on real excitation: 0.003.
9. **A third to two thirds of Kalshi "trades" are not arrivals.** Exact-tie
   fractions 13.6%–70.1%, median 35.2%. `<1ms` equals `exact ties` in every
   market — identical timestamps, several fills from one aggressive order.
   **Fitting raw prints is degenerate**: reproduced against known truth
   (n=0.500, β=1.6), raw prints give n=0.664, β=198,000, half-life 3.5 µs — and
   **report convergence**.
10. **Two regimes.** Busiest pinned market: 8 trades/hour. Exchange-wide:
    387,000/hour. A factor of ~48,000. The two projects want opposite data and
    both datasets exist.
11. **First real fits (9 markets, slow arm):** 9/9 beat Poisson on held-out
    likelihood — clustering is real. But **only 5 of 18 windows pass the
    diagnostics**, and the branching ratios are **bimodal**: 4 markets at n≈0.88
    with half-lives 975–6,221 s, 5 at n≈0.23 with half-lives 11–1,849 s, nothing
    between. Deseasonalising moves the median per-market by **+0.004**.

**Finding 11 is the current frontier.** Bimodal n tracking half-life, systematic
diagnostic rejection, and seasonality doing nothing are together the signature of
**long memory** that an exponential kernel cannot represent. See
`docs/power_law_spec.md`.

---

## What to do next, in priority order

1. **Power-law kernel.** Fully specified in `docs/power_law_spec.md`, including
   the sum-of-exponentials approximation that keeps the likelihood tractable and
   the validation sequence. This is the critical path and it is the difference
   between a competent replication and a contribution.
2. **Fast-arm fit.** `data_fast/raw/`, 3.56M events. `--period 3600`,
   `--window-events 20000`. The slow/fast contrast is Project 2's thesis.
3. **Verify YES/NO order book complementarity.** The schema stores one book in
   YES terms and derives the NO side from `ask = 100 − no_bid`. **This is
   assumed, not verified**, and every price in Project 1 rests on it. Raw
   payloads are on disk; this is an afternoon.
4. **Bucket-sum detector**, short direction. Write now, run in a week when there
   is enough order book series to ask whether the gap ever crosses zero.
5. **`docs/results_summary.md`** — one page. After the power-law result.

Blocked: fee reconciliation against a settled fill (needs a real trade).

---

## Traps already hit — do not re-discover these

- **The API's documented field names do not exist in live payloads.** Real
  fields are unit-suffixed: `yes_ask_dollars`, `volume_fp`, `count_fp`. A parser
  written from the docs returned **zero rows across 4,987 events** and looked
  like "no data" rather than "wrong parser".
- **Kalshi signs a millisecond timestamp.** Clock drift returns 401 on every
  request and reads exactly like a bad API key. Check `timedatectl` first.
- **`--min-days-to-close` is not optional for a panel.** Volume-ranked selection
  fills the universe with same-day sports: a universe pinned on 08-24 was 38/150
  already-settled.
- **Pin the universe or the panel churns.** Discovery re-selects on every start;
  across three restarts, 298 tickers and **zero** spanned the full window.
- **Unbounded log-parameters let L-BFGS-B report success at α ≈ 1e190.**
  Invisible in a single 20,000-event fit, fatal at 2,000. Bounds are derived from
  identifiability limits, not tuning.
- **The β bound must use a low quantile of gaps, not the minimum.** One
  near-simultaneous pair drags the minimum to microseconds.
- **Thresholds must be applied after aggregation, not before.** Filtering on
  prints admitted markets that fell to 630 orders after merging.
- **"0 errors" in a log is not a coverage claim.** Check the files
  (`scripts/audit_coverage.py`).
- **Never quote a statistic you have not defined.** The fit summary printed
  "shrinkage +0.145" (difference of medians) where the real median per-market
  shrink was +0.004.

---

## Working conventions

- **Every number reported must be traceable to code or data.** Nothing asserted.
- **Sections that are not done say "not yet done"** rather than being omitted —
  a reader must be able to tell "no edge here" from "not looked at yet".
- **Report both tests.** KS cannot see autocorrelation; Ljung-Box is required
  alongside it. A fit passes only if neither rejects.
- **A rejection is decisive; a pass is weak evidence** (parameters were fitted on
  the same data).
- **Simulate before fitting.** Every estimator is validated against data whose
  answer is known before it touches real data.
- Comments explain *why*, especially where a choice looks arbitrary.

## Repo layout

```
src/quant/
  common/api/kalshi.py        REST client, RSA-PSS signing, trade parsing
  common/db/schema.py         Pydantic models (frozen; derived values are properties)
  ingest/discovery.py         Market selection, family classification
  ingest/kalshi_collector.py  Order book collector
  ingest/trade_backfill.py    Trade backfill, windowed and resumable
  kalshi/fees.py              Exact-decimal fee model
  diffusion/hawkes/
    simulation.py             Cluster construction + Ogata thinning
    model.py                  MLE, O(n) recursion, held-out likelihood
    baseline.py               Seasonal profile + time change
    preprocess.py             Print → order aggregation
    diagnostics.py            Time-rescaling, KS + Ljung-Box
scripts/
  collect_kalshi.py           Collector CLI
  backfill_trades.py          Trade backfill CLI
  audit_coverage.py           Coverage audit (stdlib only)
  hawkes_recovery.py          4 validation studies
  fit_diffusion.py            Real-data fitting driver
deploy/                       systemd units, install.sh, watchdog
docs/                         Methodology, findings, specs
```

## Commands

```powershell
# fit the slow arm (Windows, where numpy/scipy live)
.venv\Scripts\python.exe scripts\fit_diffusion.py --data .\data --min-trades 2000

# inspect timestamp tie structure before fitting anything
.venv\Scripts\python.exe scripts\fit_diffusion.py --data .\data --min-trades 2000 --ties

# estimator validation, 4 studies
.venv\Scripts\python.exe scripts\hawkes_recovery.py --reps 100

# pull fresh data off the server
scp -i $env:USERPROFILE\.ssh\oracle_key ubuntu@159.54.168.0:/opt/quant-research/data/raw/kalshi_trades_*.jsonl .\data\raw\
```

```bash
# on the server
sudo -u collector python3 scripts/audit_coverage.py --data ./data --per-market
journalctl -u kalshi-collector -f
systemctl show kalshi-collector -p NRestarts
```

## Security

- `.env`, `certs/`, `data/` are gitignored and **verified** not in the repo.
- The Kalshi private key **can place real trades**. It lives at
  `/opt/quant-research/certs/kalshi_prod.pem` (server) and `certs/` (Windows),
  mode 600, owned by `collector`. Never paste its contents anywhere — move it
  as a file with `scp` only.



==============================================================================
# THE TASK

`docs/power_law_spec.md`
==============================================================================

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

Grid suggestion: `M = 20–30`, `beta` spanning `1/(10T)` up to `10/median_gap`.
Check convergence by re-fitting with `M` doubled — if `n` moves materially, the
grid is too coarse.

### Likelihood with M components

Every component keeps its own recursion, so:

```
lambda(t_i) = mu + sum_j c_j * A_j(i)
A_j(i)      = exp(-beta_j * dt_i) * (1 + A_j(i-1))
```

Cost O(n·M) — with M = 25 that is 25× the exponential fit, which is acceptable.

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



==============================================================================
# SOURCE: hawkes/model.py — the exponential estimator to mirror

`src/quant/diffusion/hawkes/model.py`
==============================================================================

```python
"""Maximum-likelihood estimation for the exponential-kernel Hawkes process.

The conditional intensity is

    lambda(t) = mu + sum_{t_i < t} alpha * exp(-beta * (t - t_i))

and the log-likelihood on ``[0, T]`` for events ``t_1 < ... < t_n`` is

    log L = -mu * T
            - (alpha / beta) * sum_i (1 - exp(-beta * (T - t_i)))
            + sum_i log(mu + alpha * A_i)

where ``A_i = sum_{j < i} exp(-beta * (t_i - t_j))``.

Written naively that sum is O(n^2), which is the difference between a fit that
takes a second and one that takes an hour on a few hundred thousand trades. The
exponential kernel admits the recursion

    A_1 = 0,    A_i = exp(-beta * (t_i - t_{i-1})) * (1 + A_{i-1})

which makes the whole likelihood O(n). That recursion is the only reason the
exponential kernel is the standard choice; a power-law kernel fits reflexivity
data better but costs the recursion.

Two deliberate choices about what is *not* enforced:

* **Stationarity is not constrained.** A fitted branching ratio at or above one
  is reported as-is. It is the single most informative diagnostic the fit
  produces - usually about a misspecified kernel or a window spanning a regime
  change - and constraining it away would replace a finding with a boundary
  solution that looks like a result.
* **Multiple starts, not one.** The Hawkes likelihood is multimodal in
  ``beta``, and a single start lands in a local optimum often enough that a
  one-start fit is not trustworthy. Starts are spread over decades of timescale.
"""

from __future__ import annotations

import logging
import math
from typing import Literal, Optional, Sequence

import numpy as np
from scipy.optimize import minimize

from quant.common.db.schema import HawkesParameters

log = logging.getLogger(__name__)

# Guards the log() in the likelihood. An intensity can legitimately approach
# zero between distant events; it can never be non-positive.
_FLOOR = 1e-300

# Returned for infeasible parameters. Large enough never to win, small enough
# that L-BFGS-B's finite-difference gradient stays finite - 1e300 produces
# inf-inf in the difference quotient and a NaN gradient, which stalls the
# line search instead of turning it around.
_PENALTY = 1e100


def _decay_states(times: np.ndarray, beta: float) -> np.ndarray:
    """The recursion ``A_i = exp(-beta * dt) * (1 + A_{i-1})``, vectorised head."""
    n = len(times)
    states = np.empty(n, dtype=float)
    states[0] = 0.0
    if n == 1:
        return states
    decays = np.exp(-beta * np.diff(times))
    acc = 0.0
    for i in range(1, n):
        acc = decays[i - 1] * (1.0 + acc)
        states[i] = acc
    return states


def log_likelihood(
    times: np.ndarray, mu: float, alpha: float, beta: float, T: float
) -> float:
    """Exact log-likelihood of an exponential-kernel Hawkes process."""
    if mu <= 0 or alpha <= 0 or beta <= 0:
        return -np.inf
    n = len(times)
    if n == 0:
        return -mu * T

    states = _decay_states(times, beta)
    intensities = mu + alpha * states
    if np.any(intensities <= 0):
        return -np.inf

    compensator = mu * T + (alpha / beta) * np.sum(
        1.0 - np.exp(-beta * (T - times))
    )
    return float(np.sum(np.log(np.maximum(intensities, _FLOOR))) - compensator)


def compensator(
    times: np.ndarray, mu: float, alpha: float, beta: float, upto: float
) -> float:
    """Integrated intensity ``Lambda(upto)``, used by the diagnostics."""
    past = times[times < upto]
    return float(
        mu * upto + (alpha / beta) * np.sum(1.0 - np.exp(-beta * (upto - past)))
    )


def log_likelihood_on_interval(
    times: np.ndarray,
    mu: float,
    alpha: float,
    beta: float,
    t_start: float,
    t_end: float,
) -> float:
    """Log-likelihood of the events in ``(t_start, t_end]``, given ALL history.

    This is what a held-out comparison needs, and it is not the same as fitting
    the tail separately. Events before ``t_start`` still excite the process
    inside the window, so a test-set likelihood that discards them measures a
    different model from the one that was fitted. The conditioning history is
    the whole array; only the summation range is restricted.

    A Hawkes fit that cannot beat a homogeneous Poisson process on held-out
    likelihood has demonstrated nothing, whatever its in-sample branching ratio
    says - which is why this exists before any real-data fit is reported.
    """
    times = np.asarray(times, dtype=float)
    if mu <= 0 or alpha <= 0 or beta <= 0 or t_end <= t_start:
        return -np.inf
    if len(times) == 0:
        return -mu * (t_end - t_start)

    states = _decay_states(times, beta)
    intensities = mu + alpha * states
    in_window = (times > t_start) & (times <= t_end)
    if np.any(intensities[in_window] <= 0):
        return -np.inf

    log_terms = float(np.sum(np.log(np.maximum(intensities[in_window], _FLOOR))))
    integrated = compensator(times, mu, alpha, beta, t_end) - compensator(
        times, mu, alpha, beta, t_start
    )
    return log_terms - integrated


def poisson_log_likelihood_on_interval(
    times: np.ndarray, rate: float, t_start: float, t_end: float
) -> float:
    """Homogeneous Poisson log-likelihood on ``(t_start, t_end]``.

    The null the Hawkes fit has to beat. Deliberately the simplest possible
    alternative: if self-excitation is real it should clear a constant rate by
    a wide margin, and if it does not, the branching ratio is describing noise.
    """
    times = np.asarray(times, dtype=float)
    if rate <= 0 or t_end <= t_start:
        return -np.inf
    count = int(np.sum((times > t_start) & (times <= t_end)))
    return count * math.log(rate) - rate * (t_end - t_start)


def _neg_ll_log_params(params: np.ndarray, times: np.ndarray, T: float) -> float:
    """Negative log-likelihood in log-parameter space.

    Optimising ``log mu, log alpha, log beta`` keeps every parameter positive
    without a constrained solver, and rescales the problem so that beta - which
    ranges over orders of magnitude - is not fighting mu for step size.
    """
    mu, alpha, beta = np.exp(params)
    if not np.all(np.isfinite([mu, alpha, beta])):
        return _PENALTY
    value = -log_likelihood(times, mu, alpha, beta, T)
    return _PENALTY if not math.isfinite(value) else value


def _bounds(times: np.ndarray, T: float) -> list[tuple[float, float]]:
    """Finite box constraints on the log-parameters.

    Without these, L-BFGS-B walks into the region where ``exp(log_beta)``
    overflows, the objective returns a constant penalty, the gradient there is
    exactly zero, and the optimiser reports *success* at a parameter value of
    order 1e190. Point estimates then look plausible in aggregate while the
    variance is infinite - which is how a validation study catches a bug that a
    single well-behaved fit hides. The 20,000-event sample this estimator was
    first tried on converged perfectly; the failure only appeared at 2,000.

    The bounds are not tuning knobs. Each marks a genuine edge of
    identifiability:

    * ``beta`` far below ``1 / T`` means excitation that never decays within the
      observation window, which is indistinguishable from a larger baseline.
    * ``beta`` far above the reciprocal of the smallest observed gap means
      excitation that has vanished before the next event could see it, which is
      indistinguishable from no excitation at all.
    * ``mu`` cannot exceed the total observed arrival rate; the baseline is a
      component of that rate, not a free parameter above it.
    * ``alpha / beta`` above ~20 is explosive by any standard. The bound is kept
      well clear of 1 so that a supercritical fit is still reportable rather
      than clipped into looking stationary.
    """
    n = len(times)
    rate = n / T
    gaps = np.diff(times)
    positive = gaps[gaps > 0]
    # A LOW QUANTILE, not the strict minimum. The minimum is a single order
    # statistic and one near-simultaneous pair drags it to microseconds, which
    # sends the beta ceiling to millions and hands the optimiser a degenerate
    # direction to run in. On the first live fit of Kalshi trade prints every
    # window in every market pinned the beta bound this way, because a single
    # aggressive order emits several prints at the same instant. Aggregating
    # prints into orders is the real fix (see hawkes.preprocess); this makes
    # the bound robust to whatever ties survive it.
    if len(positive) >= 100:
        smallest = float(np.quantile(positive, 0.01))
    elif len(positive):
        smallest = float(positive.min())
    else:
        smallest = T / max(n, 1)
    typical = float(np.median(positive)) if len(positive) else T / max(n, 1)

    beta_lo = 1e-3 / T
    beta_hi = 1e3 / max(smallest, T * 1e-12)
    mu_lo = 1e-8 * rate
    mu_hi = 10.0 * rate
    alpha_lo = 1e-8 * beta_lo
    alpha_hi = 20.0 * beta_hi

    del typical  # kept above for readability of the reasoning; not a bound
    return [
        (math.log(mu_lo), math.log(mu_hi)),
        (math.log(alpha_lo), math.log(alpha_hi)),
        (math.log(beta_lo), math.log(beta_hi)),
    ]


def _at_bound(x: np.ndarray, bounds: list[tuple[float, float]], tol: float = 1e-6) -> bool:
    """Whether the solution sits on the edge of the feasible box.

    A boundary solution is not an interior optimum, so its Hessian-based
    standard errors are meaningless. Reported as non-convergence rather than
    quietly returned as a fit.
    """
    return any(
        abs(value - lo) < tol * max(1.0, abs(lo))
        or abs(value - hi) < tol * max(1.0, abs(hi))
        for value, (lo, hi) in zip(x, bounds)
    )


def _numeric_hessian(f, x: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """Central-difference Hessian.

    Used instead of the optimiser's inverse-Hessian approximation, which
    L-BFGS-B builds from a limited memory of gradient steps and is not reliable
    enough to quote standard errors from.
    """
    n = len(x)
    hess = np.zeros((n, n))
    steps = np.maximum(np.abs(x) * eps, eps)
    for i in range(n):
        for j in range(i, n):
            xi, xj = np.zeros(n), np.zeros(n)
            xi[i], xj[j] = steps[i], steps[j]
            value = (
                f(x + xi + xj) - f(x + xi - xj) - f(x - xi + xj) + f(x - xi - xj)
            ) / (4.0 * steps[i] * steps[j])
            hess[i, j] = hess[j, i] = value
    return hess


def _standard_errors(
    times: np.ndarray, T: float, log_params: np.ndarray
) -> dict[str, float]:
    """Asymptotic standard errors on the natural parameters.

    The Hessian is taken in log space and mapped back by the delta method: for
    ``theta = exp(p)``, ``Var(theta) = theta^2 * Var(p)``. Returns an empty dict
    when the Hessian is not positive definite, which happens at a boundary or a
    flat optimum - reporting nothing is better than reporting a standard error
    derived from a negative variance.
    """
    try:
        hess = _numeric_hessian(lambda p: _neg_ll_log_params(p, times, T), log_params)
        cov = np.linalg.inv(hess)
        variances = np.diag(cov)
        if np.any(variances <= 0) or not np.all(np.isfinite(variances)):
            log.warning("non-positive variance in the Hessian; omitting std errors")
            return {}
        theta = np.exp(log_params)
        natural = np.sqrt(variances) * theta
        return {
            "mu": float(natural[0]),
            "alpha": float(natural[1]),
            "beta": float(natural[2]),
        }
    except np.linalg.LinAlgError:
        log.warning("singular Hessian; omitting std errors")
        return {}


def _initial_guesses(times: np.ndarray, T: float) -> list[np.ndarray]:
    """Starting points spread over decades of timescale.

    ``beta`` is the parameter that traps a single-start optimiser. Its scale is
    set by how fast excitation decays, which is unknown a priori and can differ
    by orders of magnitude between a fast order-flow cascade and a slow news
    cycle. Anchoring on the median inter-arrival time and then sweeping decades
    around it covers both without assuming either.
    """
    rate = len(times) / T
    gaps = np.diff(times)
    typical = float(np.median(gaps)) if len(gaps) else 1.0 / max(rate, 1e-9)
    typical = max(typical, 1e-9)

    guesses = []
    for beta_scale in (0.1, 1.0, 10.0, 100.0):
        beta = beta_scale / typical
        for n0 in (0.3, 0.6, 0.9):
            mu = max(rate * (1.0 - n0), 1e-9)
            alpha = n0 * beta
            guesses.append(np.log([mu, alpha, beta]))
    return guesses


def fit(
    times: Sequence[float] | np.ndarray,
    T: Optional[float] = None,
    time_unit: Literal["second", "minute", "hour", "day"] = "second",
    compute_std_errors: bool = True,
) -> HawkesParameters:
    """Fit an exponential-kernel Hawkes process by maximum likelihood.

    Args:
        times: Event times in ``time_unit``, measured from the start of the
            observation window and sorted ascending.
        T: Length of the observation window. Defaults to the last event time,
            which is a *downward-biased* choice: conditioning the window on the
            data slightly overstates the rate. Pass the real window whenever it
            is known.
        time_unit: Unit the results are expressed in. Recorded on the result so
            a branching ratio is never compared against one fitted in different
            units.
        compute_std_errors: Numerical Hessian, roughly 36 extra likelihood
            evaluations. Turn it off in a tight simulation loop.

    Returns:
        The fitted :class:`HawkesParameters`, whose ``branching_ratio``,
        ``is_stationary`` and ``excitation_half_life`` are derived rather than
        stored.

    Raises:
        ValueError: If fewer than two events are supplied, or the times are not
            sorted and within the window.
    """
    times = np.asarray(times, dtype=float)
    if len(times) < 2:
        raise ValueError(f"need at least 2 events to fit, got {len(times)}")
    if np.any(np.diff(times) < 0):
        raise ValueError("times must be sorted ascending")
    if times[0] < 0:
        raise ValueError("times must be measured from the start of the window")

    if T is None:
        T = float(times[-1])
        log.warning(
            "no observation window given; using the last event time (%.3f). This "
            "biases the rate upward slightly - pass the true window if known.",
            T,
        )
    if T < times[-1]:
        raise ValueError(f"window T={T} ends before the last event {times[-1]}")

    bounds = _bounds(times, T)
    best = None
    for start in _initial_guesses(times, T):
        # Clip the start into the box; a start outside it is silently projected
        # by the solver anyway, and clipping makes that explicit.
        start = np.clip(start, [lo for lo, _ in bounds], [hi for _, hi in bounds])
        result = minimize(
            _neg_ll_log_params,
            start,
            args=(times, T),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 2000, "ftol": 1e-12},
        )
        if not np.isfinite(result.fun) or result.fun >= _PENALTY:
            continue
        if best is None or result.fun < best.fun:
            best = result

    if best is None:
        raise RuntimeError("every starting point diverged; check the input times")

    mu, alpha, beta = np.exp(best.x)
    on_bound = _at_bound(best.x, bounds)
    if on_bound:
        log.warning(
            "fit rests on a parameter bound (mu=%.4g alpha=%.4g beta=%.4g). "
            "Reported as not converged: a boundary solution has no interior "
            "optimum and its standard errors would be meaningless.",
            mu,
            alpha,
            beta,
        )

    std_errors = (
        _standard_errors(times, T, best.x)
        if compute_std_errors and not on_bound
        else {}
    )

    params = HawkesParameters(
        mu=float(mu),
        alpha=float(alpha),
        beta=float(beta),
        time_unit=time_unit,
        log_likelihood=float(-best.fun),
        n_events=len(times),
        observation_window=float(T),
        converged=bool(best.success) and not on_bound,
        std_errors=std_errors,
    )

    if not params.is_stationary:
        log.warning(
            "branching ratio %.4f >= 1. This is a diagnostic, not a crash: it "
            "usually means the exponential kernel is misspecified for this data "
            "or the window spans a regime change.",
            params.branching_ratio,
        )
    return params

```


==============================================================================
# SOURCE: hawkes/simulation.py — simulators, extend for power law

`src/quant/diffusion/hawkes/simulation.py`
==============================================================================

```python
"""Simulate a univariate Hawkes process with an exponential kernel.

Two algorithms, for two different jobs.

:func:`simulate_cluster` uses the immigrant-offspring branching construction.
Every event knows its parent, so a simulated sample carries the ground truth
that real data never can. This is the sample the estimator is validated against:
if MLE cannot recover parameters it generated itself, no result it produces on
Kalshi trades means anything. It requires ``alpha / beta < 1``, because a
supercritical process produces infinite descendants and the recursion does not
terminate.

:func:`simulate_thinning` uses Ogata's thinning algorithm. It makes no
stationarity assumption, so it is the one to reach for when deliberately
simulating near or above the critical point - which matters here, because
published order-flow branching ratios sit close enough to 1 that estimator
behaviour in that regime is a real question rather than a corner case.

Both take an explicit ``seed``. A simulation study whose numbers cannot be
reproduced is an anecdote.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np


def simulate_cluster(
    mu: float,
    alpha: float,
    beta: float,
    T: float,
    seed: Optional[int] = None,
    max_events: int = 2_000_000,
) -> tuple[np.ndarray, np.ndarray]:
    """Simulate by immigrant-offspring branching.

    Args:
        mu: Baseline (immigrant) intensity, events per unit time.
        alpha: Excitation amplitude.
        beta: Kernel decay rate.
        T: Length of the observation window, starting at zero.
        seed: PRNG seed.
        max_events: Abort above this many events, rather than exhausting memory.

    Returns:
        ``(times, parents)`` sorted by time. ``parents`` holds the index into
        ``times`` of each event's trigger, or ``-1`` for an immigrant.

    Raises:
        ValueError: If the branching ratio is at or above one, where the
            construction does not terminate.
    """
    n = alpha / beta
    if n >= 1.0:
        raise ValueError(
            f"branching ratio {n:.4f} >= 1: the cluster construction produces "
            "infinitely many offspring. Use simulate_thinning for the "
            "supercritical case."
        )
    if T <= 0:
        raise ValueError("T must be positive")

    rng = np.random.default_rng(seed)

    n_immigrants = rng.poisson(mu * T)
    times = list(rng.uniform(0.0, T, size=n_immigrants))
    parents = [-1] * n_immigrants

    # Breadth-first over generations. Each event spawns Poisson(n) offspring at
    # Exp(beta) delays; offspring past T are discarded but their own subtrees
    # are not explored, which is correct because delays are strictly positive.
    frontier = list(range(len(times)))
    while frontier:
        if len(times) > max_events:
            raise ValueError(
                f"simulation exceeded {max_events} events; branching ratio "
                f"{n:.4f} with T={T} is too large for this window"
            )
        next_frontier: list[int] = []
        counts = rng.poisson(n, size=len(frontier))
        for parent_idx, count in zip(frontier, counts):
            if count == 0:
                continue
            delays = rng.exponential(1.0 / beta, size=count)
            for delay in delays:
                child = times[parent_idx] + delay
                if child >= T:
                    continue
                times.append(child)
                parents.append(parent_idx)
                next_frontier.append(len(times) - 1)
        frontier = next_frontier

    if not times:
        return np.empty(0), np.empty(0, dtype=int)

    order = np.argsort(times, kind="stable")
    sorted_times = np.asarray(times, dtype=float)[order]

    # Parent indices refer to pre-sort positions and must be remapped, or every
    # downstream check of the branching structure silently reads the wrong
    # ancestor.
    remap = np.empty(len(order), dtype=int)
    remap[order] = np.arange(len(order))
    old_parents = np.asarray(parents, dtype=int)
    sorted_parents = np.where(old_parents < 0, -1, remap[np.maximum(old_parents, 0)])[
        order
    ]
    return sorted_times, sorted_parents


def simulate_thinning(
    mu: float,
    alpha: float,
    beta: float,
    T: float,
    seed: Optional[int] = None,
    max_events: int = 2_000_000,
) -> np.ndarray:
    """Simulate by Ogata's thinning algorithm.

    Valid for any parameters, including the supercritical case, though a
    supercritical process will hit ``max_events`` rather than finish gracefully.

    The upper bound used for thinning is the intensity immediately after the
    current time. Between events the intensity only decays, so that value
    dominates the intensity everywhere until the next accepted event, which is
    what makes the acceptance step exact rather than approximate.
    """
    if T <= 0:
        raise ValueError("T must be positive")

    rng = np.random.default_rng(seed)
    times: list[float] = []
    t = 0.0
    decay_sum = 0.0  # sum of exp(-beta * (t - t_i)) over accepted events
    last = 0.0

    while True:
        intensity_now = mu + alpha * decay_sum
        if intensity_now <= 0:
            break
        t = t + rng.exponential(1.0 / intensity_now)
        if t >= T:
            break

        decayed = decay_sum * math.exp(-beta * (t - last))
        last = t
        decay_sum = decayed

        if rng.uniform(0.0, intensity_now) <= mu + alpha * decay_sum:
            times.append(t)
            decay_sum += 1.0
            if len(times) > max_events:
                raise ValueError(
                    f"simulation exceeded {max_events} events; the process is "
                    f"likely supercritical (branching ratio {alpha / beta:.4f})"
                )

    return np.asarray(times, dtype=float)

```


==============================================================================
# SOURCE: hawkes/diagnostics.py — reuse unchanged

`src/quant/diffusion/hawkes/diagnostics.py`
==============================================================================

```python
"""Goodness-of-fit for a fitted point process, via the time-rescaling theorem.

The theorem: if the fitted conditional intensity is the true one, then the
compensator-transformed event times

    tau_k = Lambda(t_k) = integral of lambda(s) ds from 0 to t_k

have inter-arrival times that are independent and identically distributed
unit-rate exponential. So a Hawkes fit can be tested by transforming the data
through its own fitted intensity and asking whether what comes out is
indistinguishable from a Poisson process.

**Two tests, because the theorem makes two claims.** The KS test checks the
marginal distribution - that the rescaled gaps are unit exponential. It says
nothing about whether they are independent, and a badly misspecified kernel can
easily produce correctly-distributed gaps that remain autocorrelated. That
second claim needs a Ljung-Box test on the rescaled series. Reporting only the
KS test is the standard way a Hawkes fit passes validation it should have
failed, so both are computed here and both are reported.

A caveat that belongs in the write-up rather than a footnote: the parameters
being tested were estimated from the same data. That makes the KS p-value
optimistic, because the fit has already spent some of its freedom matching
these residuals. Treat a rejection as decisive and a pass as weak evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from scipy import stats

from quant.common.db.schema import HawkesParameters, ResidualDiagnostics
from quant.diffusion.hawkes.model import _decay_states

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RescaledResiduals:
    """Rescaled inter-arrival times plus everything needed to plot them."""

    intervals: np.ndarray
    """Rescaled inter-arrival times; unit-rate exponential under the null."""

    uniforms: np.ndarray
    """``1 - exp(-interval)``; standard uniform under the null. Q-Q ready."""

    ks: ResidualDiagnostics
    """Kolmogorov-Smirnov test against the unit exponential."""

    ljung_box: ResidualDiagnostics
    """Ljung-Box test for autocorrelation in the rescaled series."""

    autocorrelations: np.ndarray
    """Sample autocorrelation of ``intervals`` at lags 1..h, for plotting."""

    @property
    def passes(self) -> bool:
        """True only when neither test rejects.

        Deliberately conjunctive. A fit that produces unit-exponential but
        autocorrelated residuals has not been validated, whatever the KS
        p-value says.
        """
        return not (self.ks.rejects_null or self.ljung_box.rejects_null)

    def report(self) -> str:
        verdict = "consistent with the fit" if self.passes else "REJECTS the fit"
        return (
            f"{self.ks.n_residuals} rescaled residuals: {verdict}\n"
            f"  KS vs Exp(1)   D={self.ks.statistic:.4f}  p={self.ks.p_value:.4f}\n"
            f"  Ljung-Box      Q={self.ljung_box.statistic:.2f}  "
            f"p={self.ljung_box.p_value:.4f}\n"
            f"  mean {self.intervals.mean():.4f} (1.0 under the null), "
            f"var {self.intervals.var():.4f} (1.0 under the null)"
        )


def rescale(
    times: Sequence[float] | np.ndarray,
    mu: float,
    alpha: float,
    beta: float,
) -> np.ndarray:
    """Rescaled inter-arrival times ``Lambda(t_k) - Lambda(t_{k-1})``.

    Computed by recursion rather than by evaluating the compensator at each
    event. Expanding

        Lambda(t_k) - Lambda(t_{k-1})
            = mu * dt + (alpha / beta) * (1 + A_{k-1}) * (1 - exp(-beta * dt))

    reuses the same ``A`` state the likelihood already maintains, so the whole
    residual series costs O(n) instead of O(n^2).
    """
    times = np.asarray(times, dtype=float)
    if len(times) == 0:
        return np.empty(0)

    states = _decay_states(times, beta)
    gaps = np.diff(times)

    intervals = np.empty(len(times), dtype=float)
    # The first event has no predecessor: Lambda(t_0) - Lambda(0) is just the
    # baseline, since no event has yet occurred to excite anything.
    intervals[0] = mu * times[0]
    if len(times) > 1:
        intervals[1:] = mu * gaps + (alpha / beta) * (1.0 + states[:-1]) * (
            1.0 - np.exp(-beta * gaps)
        )
    return intervals


def _ljung_box(x: np.ndarray, lags: int) -> tuple[float, float, np.ndarray]:
    """Ljung-Box Q statistic, its p-value, and the autocorrelations used."""
    n = len(x)
    centred = x - x.mean()
    denom = float(np.dot(centred, centred))
    if denom == 0:
        return 0.0, 1.0, np.zeros(lags)

    acf = np.array(
        [float(np.dot(centred[:-k], centred[k:])) / denom for k in range(1, lags + 1)]
    )
    q = n * (n + 2) * float(np.sum(acf**2 / (n - np.arange(1, lags + 1))))
    p_value = float(stats.chi2.sf(q, df=lags))
    return q, p_value, acf


def check_fit(
    times: Sequence[float] | np.ndarray,
    params: HawkesParameters,
    alpha_level: float = 0.05,
    lags: Optional[int] = None,
) -> RescaledResiduals:
    """Run the full time-rescaling diagnostic on a fitted model.

    Args:
        times: The same event times the model was fitted to.
        params: The fitted parameters.
        alpha_level: Significance level for both tests.
        lags: Ljung-Box lag count. Defaults to ``min(20, n // 5)``, the usual
            rule of thumb; too many lags dilutes power, too few miss slow
            structure.

    Returns:
        Both tests, the residuals, and the autocorrelations, in a form that can
        go straight into a reliability plot.
    """
    times = np.asarray(times, dtype=float)
    intervals = rescale(times, params.mu, params.alpha, params.beta)
    n = len(intervals)
    if n < 2:
        raise ValueError(f"need at least 2 residuals, got {n}")

    ks_stat, ks_p = stats.kstest(intervals, "expon", args=(0.0, 1.0))

    if lags is None:
        lags = max(1, min(20, n // 5))
    q_stat, q_p, acf = _ljung_box(intervals, lags)

    note = (
        "Parameters were estimated from these same events, so the KS p-value is "
        "optimistic; a rejection is decisive, a pass is weak evidence."
    )

    return RescaledResiduals(
        intervals=intervals,
        uniforms=1.0 - np.exp(-intervals),
        ks=ResidualDiagnostics(
            test_name="kolmogorov_smirnov_vs_unit_exponential",
            statistic=float(ks_stat),
            p_value=float(ks_p),
            n_residuals=n,
            alpha_level=alpha_level,
            notes=note,
        ),
        ljung_box=ResidualDiagnostics(
            test_name=f"ljung_box_lag{lags}",
            statistic=float(q_stat),
            p_value=float(q_p),
            n_residuals=n,
            alpha_level=alpha_level,
            notes=(
                "Tests independence of the rescaled series. The KS test cannot "
                "see autocorrelation, so a KS pass alone does not validate a fit."
            ),
        ),
        autocorrelations=acf,
    )

```


==============================================================================
# SOURCE: hawkes/baseline.py — seasonal time change, reuse unchanged

`src/quant/diffusion/hawkes/baseline.py`
==============================================================================

```python
"""Separating a time-varying baseline from genuine self-excitation.

This module exists because of a measured result, not a theoretical worry. On
simulated arrivals with **zero** self-excitation - an inhomogeneous Poisson
process whose rate varies sinusoidally - a constant-baseline Hawkes fit reports
a branching ratio of **0.79**, and the residual diagnostics reject it only 12%
of the time. Published order-flow branching ratios sit at 0.8-0.9. A fit to raw
Kalshi trade times inherits that bias and is not reportable.

The correction used here is a **seasonal time change**, which is the standard
treatment and is exact rather than approximate.

Write the intensity as a periodic baseline times a self-exciting part:

    lambda(t) = s(t) * [ mu + sum_{t_i < t} alpha * exp(-beta * (tau(t) - tau(t_i))) ]

where ``s`` is a periodic seasonality factor normalised to mean 1, and

    tau(t) = integral_0^t s(u) du

is the *operational time* it induces. Transforming the event times through
``tau`` makes the baseline constant by construction, so an ordinary
constant-baseline Hawkes fit on the transformed times is correctly specified
with respect to seasonality.

Two properties make this worth doing rather than the alternatives:

* **The branching ratio is invariant under the time change.** ``n`` counts
  expected direct offspring per event, which is a property of the branching
  structure and not of the clock. A monotone reparameterisation of time cannot
  change how many children an event has. So ``n`` fitted in operational time is
  directly comparable to ``n`` fitted anywhere else - which is exactly the
  quantity being compared against the literature.
* **``beta`` is not invariant.** It is a rate, expressed per unit of operational
  time. :func:`SeasonalProfile.mean_rate_scale` gives the factor needed to talk
  about half-lives in wall-clock seconds again, and any reported half-life must
  state which clock it is on.

The estimator for ``s`` is deliberately nonparametric - a binned rate by phase
within the period. Fitting a parametric seasonal shape would mean choosing a
functional form for exactly the thing that is being controlled for, and getting
that form wrong reintroduces the bias it was meant to remove.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

log = logging.getLogger(__name__)

SECONDS_PER_DAY = 86_400.0

# Below this many events per bin the binned rate is dominated by counting noise,
# and "estimating" seasonality from it manufactures structure rather than
# removing it. See SeasonalProfile.estimate.
MIN_EVENTS_PER_BIN = 20


@dataclass(frozen=True)
class SeasonalProfile:
    """A piecewise-constant periodic intensity multiplier, normalised to mean 1.

    ``factors[k]`` is the relative arrival rate during phase bin ``k`` of the
    period. A flat profile (all ones) means no seasonality was estimated, which
    is reported rather than silently applied.
    """

    factors: np.ndarray
    period: float
    """Period in the same time unit as the event times."""

    n_events: int
    is_flat: bool
    """True when estimation was declined - too few events to bin reliably."""

    reason: str = ""

    @property
    def n_bins(self) -> int:
        return len(self.factors)

    @property
    def peak_to_trough(self) -> float:
        """Ratio of busiest to quietest bin. 1.0 means no seasonality at all.

        This is the number that says whether the correction mattered. A profile
        near 1.0 means the naive and deseasonalised fits should agree, and a
        large ratio means they should not.
        """
        lo = float(self.factors.min())
        return float(self.factors.max()) / lo if lo > 0 else float("inf")

    def factor_at(self, times: np.ndarray) -> np.ndarray:
        """The seasonality multiplier in force at each time."""
        idx = self._bin_index(np.asarray(times, dtype=float))
        return self.factors[idx]

    def _bin_index(self, times: np.ndarray) -> np.ndarray:
        phase = np.mod(times, self.period) / self.period
        idx = (phase * self.n_bins).astype(int)
        # Guard the closed upper edge: phase exactly 1.0 would index past the end.
        return np.clip(idx, 0, self.n_bins - 1)

    @property
    def mean_rate_scale(self) -> float:
        """Operational time units per wall-clock unit, on average.

        Always 1.0 by construction, since the profile is normalised to mean 1.
        Exposed so that code converting a half-life back to wall clock states
        the conversion explicitly rather than assuming it.
        """
        return float(self.factors.mean())

    @classmethod
    def estimate(
        cls,
        times: Sequence[float] | np.ndarray,
        T: float,
        period: float = SECONDS_PER_DAY,
        n_bins: int = 24,
        min_events_per_bin: int = MIN_EVENTS_PER_BIN,
    ) -> "SeasonalProfile":
        """Estimate a periodic profile by binned rate.

        Exposure per bin is computed exactly rather than assumed uniform: an
        observation window that is not a whole number of periods gives some
        phase bins more wall-clock time than others, and dividing by a uniform
        exposure would read that asymmetry as seasonality.

        Declines to estimate - returning a flat profile with a reason - when
        there are too few events per bin. Binning 200 events into 24 bins gives
        8 per bin, where Poisson noise alone produces a peak-to-trough ratio
        near 3, and "correcting" for that fabricates structure.
        """
        times = np.asarray(times, dtype=float)
        n = len(times)

        if n < min_events_per_bin * n_bins:
            return cls(
                factors=np.ones(n_bins),
                period=period,
                n_events=n,
                is_flat=True,
                reason=(
                    f"{n} events over {n_bins} bins is under {min_events_per_bin}"
                    " per bin; binned rates would be counting noise"
                ),
            )

        counts = np.bincount(
            cls(np.ones(n_bins), period, n, True)._bin_index(times),
            minlength=n_bins,
        ).astype(float)

        exposure = _phase_exposure(T, period, n_bins)
        if np.any(exposure <= 0):
            return cls(
                factors=np.ones(n_bins),
                period=period,
                n_events=n,
                is_flat=True,
                reason="observation window does not cover every phase bin",
            )

        rates = counts / exposure
        mean_rate = rates.mean()
        if mean_rate <= 0:
            return cls(
                factors=np.ones(n_bins),
                period=period,
                n_events=n,
                is_flat=True,
                reason="zero mean rate",
            )

        factors = rates / mean_rate

        # A bin with no events would send the intensity to zero there and make
        # the time change degenerate. Floor it at a small fraction rather than
        # dropping the bin, and say so.
        floor = 1e-3
        if np.any(factors < floor):
            log.warning(
                "%d phase bin(s) had near-zero rate; floored at %.0e",
                int(np.sum(factors < floor)),
                floor,
            )
            factors = np.maximum(factors, floor)
            factors = factors / factors.mean()

        return cls(
            factors=factors,
            period=period,
            n_events=n,
            is_flat=False,
            reason="",
        )

    def to_operational_time(
        self, times: Sequence[float] | np.ndarray
    ) -> np.ndarray:
        """Map wall-clock times to operational time, ``tau(t) = int_0^t s(u) du``.

        Exact for a piecewise-constant profile: whole bins contribute their full
        width times their factor, and the partial bin containing ``t``
        contributes linearly.
        """
        times = np.asarray(times, dtype=float)
        if self.is_flat:
            return times.copy()

        bin_width = self.period / self.n_bins
        # Integral over one complete period, and the running integral at each
        # bin boundary within a period.
        per_period = float(self.factors.sum()) * bin_width
        cum_at_edge = np.concatenate([[0.0], np.cumsum(self.factors) * bin_width])

        whole_periods = np.floor(times / self.period)
        remainder = times - whole_periods * self.period
        idx = np.clip((remainder / bin_width).astype(int), 0, self.n_bins - 1)
        into_bin = remainder - idx * bin_width

        return (
            whole_periods * per_period
            + cum_at_edge[idx]
            + into_bin * self.factors[idx]
        )

    def describe(self) -> str:
        if self.is_flat:
            return f"seasonality: NOT APPLIED ({self.reason})"
        return (
            f"seasonality: {self.n_bins} bins over {self.period / 3600:.0f}h, "
            f"peak/trough {self.peak_to_trough:.2f}, "
            f"busiest bin {int(np.argmax(self.factors))}, "
            f"quietest bin {int(np.argmin(self.factors))}"
        )


def _phase_exposure(T: float, period: float, n_bins: int) -> np.ndarray:
    """Wall-clock time spent in each phase bin over ``[0, T]``.

    Computed exactly. A window of 2.5 days gives the first half of the day's
    bins three visits and the second half two, and treating exposure as uniform
    would read that purely calendrical asymmetry as a 50% seasonal effect.
    """
    bin_width = period / n_bins
    exposure = np.zeros(n_bins)

    full_periods = int(T // period)
    exposure += full_periods * bin_width

    remainder = T - full_periods * period
    if remainder > 0:
        full_bins = int(remainder // bin_width)
        exposure[:full_bins] += bin_width
        if full_bins < n_bins:
            exposure[full_bins] += remainder - full_bins * bin_width
    return exposure


def deseasonalise(
    times: Sequence[float] | np.ndarray,
    T: float,
    period: float = SECONDS_PER_DAY,
    n_bins: int = 24,
    profile: Optional[SeasonalProfile] = None,
) -> tuple[np.ndarray, float, SeasonalProfile]:
    """Transform event times into operational time.

    Returns ``(operational_times, operational_T, profile)``. Fitting a
    constant-baseline Hawkes process to the returned times is correctly
    specified with respect to seasonality; the resulting branching ratio is
    directly comparable to one fitted on any other clock, while ``beta`` and any
    half-life derived from it are in operational units.
    """
    times = np.asarray(times, dtype=float)
    if profile is None:
        profile = SeasonalProfile.estimate(times, T, period=period, n_bins=n_bins)
    return (
        profile.to_operational_time(times),
        float(profile.to_operational_time(np.array([T]))[0]),
        profile,
    )

```


==============================================================================
# SOURCE: hawkes/preprocess.py — print aggregation, reuse unchanged

`src/quant/diffusion/hawkes/preprocess.py`
==============================================================================

```python
"""Turning trade prints into a point process that can actually be fitted.

A single aggressive order that matches against several resting orders produces
several trade records at the same instant. Those are not several arrivals; they
are one arrival reported in pieces, and the distinction is fatal rather than
cosmetic.

An exponential-kernel Hawkes process has no way to represent mass at zero lag.
Faced with events separated by microseconds it does the only thing it can:
drive ``beta`` toward infinity so the excitation is tall enough and brief enough
to cover the tie. The first live fit of the Kalshi slow arm did exactly this -
**every window in every market** hit the ``beta`` upper bound, with values up to
3.8e6 per second, an implied excitation half-life around 1e-7 seconds. The
branching ratios that came with those fits were not obviously absurd, which is
what makes the failure dangerous: without the boundary check they would have
been reported.

So trades are aggregated into orders before fitting. The mark - how many prints
an order consumed - is preserved, because it is the natural size measure for a
marked extension later.

The tolerance is a judgement call and is therefore reported rather than
hidden. :func:`tie_report` prints the gap structure so the choice can be made
from the data instead of assumed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AggregatedEvents:
    """Order arrivals recovered from trade prints."""

    times: np.ndarray
    """One time per aggregated order, sorted."""

    multiplicity: np.ndarray
    """Prints consumed by each order. The natural mark for a marked model."""

    n_prints: int
    tolerance: float

    @property
    def n_events(self) -> int:
        return len(self.times)

    @property
    def merge_rate(self) -> float:
        """Fraction of prints that were absorbed into an earlier one.

        This is a fact about the exchange's reporting, not a tuning artefact,
        and it belongs in the write-up: it says how much of the apparent
        arrival rate is order flow and how much is fill reporting.
        """
        if self.n_prints == 0:
            return 0.0
        return 1.0 - self.n_events / self.n_prints

    def describe(self) -> str:
        return (
            f"{self.n_prints:,} prints -> {self.n_events:,} orders "
            f"({self.merge_rate:.1%} merged at {self.tolerance:g}s), "
            f"max {int(self.multiplicity.max())} prints/order"
        )


def aggregate_simultaneous(
    times: Sequence[float] | np.ndarray, tolerance: float = 0.001
) -> AggregatedEvents:
    """Collapse prints within ``tolerance`` seconds into single arrivals.

    Greedy and forward-only: each event opens a bucket, and every subsequent
    print within ``tolerance`` **of that bucket's opening time** joins it. The
    alternative - chaining on consecutive gaps - would let a dense burst merge
    into one arrival however long it ran, which would destroy exactly the
    clustering the model is meant to measure.

    ``tolerance=0`` merges only exact ties.
    """
    times = np.asarray(times, dtype=float)
    if len(times) == 0:
        return AggregatedEvents(times, np.empty(0), 0, tolerance)
    if np.any(np.diff(times) < 0):
        raise ValueError("times must be sorted ascending")

    keep_idx: list[int] = [0]
    counts: list[int] = [1]
    anchor = times[0]
    for i in range(1, len(times)):
        if times[i] - anchor <= tolerance:
            counts[-1] += 1
        else:
            keep_idx.append(i)
            counts.append(1)
            anchor = times[i]

    return AggregatedEvents(
        times=times[np.asarray(keep_idx)],
        multiplicity=np.asarray(counts, dtype=float),
        n_prints=len(times),
        tolerance=tolerance,
    )


def tie_report(times: Sequence[float] | np.ndarray) -> dict:
    """Describe the gap structure, so a tolerance is chosen from evidence.

    The number to look at is the exact-tie fraction. Anything above a percent
    or two means the raw series cannot be fitted directly, whatever the
    optimiser reports.
    """
    times = np.asarray(times, dtype=float)
    if len(times) < 2:
        return {}
    gaps = np.diff(times)
    n = len(gaps)
    buckets = {
        "exact_ties": float(np.sum(gaps == 0) / n),
        "under_1ms": float(np.sum(gaps < 1e-3) / n),
        "under_10ms": float(np.sum(gaps < 1e-2) / n),
        "under_100ms": float(np.sum(gaps < 1e-1) / n),
        "under_1s": float(np.sum(gaps < 1.0) / n),
    }
    positive = gaps[gaps > 0]
    buckets["min_positive_gap"] = float(positive.min()) if len(positive) else 0.0
    buckets["median_gap"] = float(np.median(gaps))
    return buckets


def format_tie_report(report: dict) -> str:
    if not report:
        return "  (too few events)"
    return (
        f"  exact ties {report['exact_ties']:.1%}  "
        f"<1ms {report['under_1ms']:.1%}  "
        f"<1s {report['under_1s']:.1%}  "
        f"min gap {report['min_positive_gap']:.4g}s  "
        f"median gap {report['median_gap']:.1f}s"
    )

```


==============================================================================
# SOURCE: scripts/fit_diffusion.py — the driver to extend

`scripts/fit_diffusion.py`
==============================================================================

```python
#!/usr/bin/env python3
"""Fit Hawkes processes to Kalshi trade arrivals, both ways.

Every fit is reported **naive and deseasonalised**, because the difference
between them is the result. On simulated arrivals with zero self-excitation, a
constant-baseline fit reports a branching ratio of 0.79 purely by absorbing a
varying rate (`scripts/hawkes_recovery.py`, study 3). So a single number here
would be uninterpretable: what matters is how much of it survives the
correction.

Three things are enforced rather than left to the reader:

1. **Windowed fitting.** Long series are fitted in windows of a fixed event
   count and the distribution of the branching ratio is reported, not a single
   number. A 90-day series is not stationary, and one fit over the whole span
   silently averages regimes. It is also the only tractable option on the fast
   arm - 3.56M events through a multi-start optimiser is hours.
2. **A held-out Poisson comparison.** A Hawkes fit that cannot beat a constant
   rate on data it was not fitted to has demonstrated nothing, whatever its
   in-sample branching ratio says.
3. **Both goodness-of-fit tests.** KS sees the marginal distribution, Ljung-Box
   sees the autocorrelation. KS alone is how a bad fit passes validation.

    # slow arm: the pinned election/Fed panel
    python scripts/fit_diffusion.py --data ./data --min-trades 2000

    # fast arm: the exchange-wide tape
    python scripts/fit_diffusion.py --data ./data --min-trades 50000 \
        --window-events 20000 --period 3600

    # one market, whole series in a single fit
    python scripts/fit_diffusion.py --data ./data --ticker KXGOVCA-26-XBEC \
        --window-events 0
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.diffusion.hawkes.baseline import (  # noqa: E402
    SECONDS_PER_DAY,
    deseasonalise,
)
from quant.diffusion.hawkes.diagnostics import check_fit  # noqa: E402
from quant.diffusion.hawkes.preprocess import (  # noqa: E402
    aggregate_simultaneous,
    format_tie_report,
    tie_report,
)
from quant.diffusion.hawkes.model import (  # noqa: E402
    fit,
    log_likelihood_on_interval,
    poisson_log_likelihood_on_interval,
)
from quant.ingest.trade_backfill import parse_trade_files  # noqa: E402

log = logging.getLogger("fit")


def load_by_market(data_dir: Path) -> dict[str, np.ndarray]:
    """Trade arrival times per market, in seconds, sorted."""
    paths = sorted((data_dir / "raw").glob("kalshi_trades_*.jsonl"))
    if not paths:
        raise SystemExit(f"no trade files in {data_dir / 'raw'}")

    per_market: dict[str, list[float]] = defaultdict(list)
    for trade in parse_trade_files(paths):
        per_market[trade.contract_id].append(trade.timestamp.timestamp())
    return {t: np.sort(np.asarray(v, dtype=float)) for t, v in per_market.items()}


def fit_one_window(
    times: np.ndarray, T: float, holdout: float
) -> dict | None:
    """Fit, diagnose, and score against Poisson on a held-out tail."""
    if len(times) < 50:
        return None

    split = T * (1.0 - holdout)
    train = times[times <= split]
    if len(train) < 25 or len(times) - len(train) < 5:
        return None

    try:
        params = fit(train, T=split, compute_std_errors=True)
    except (RuntimeError, ValueError) as exc:
        log.debug("fit failed: %s", exc)
        return None
    if not params.converged:
        return None

    # Held-out likelihood. The full history conditions the intensity; only the
    # summation range is the test window. A Poisson rate estimated on the same
    # training data is the null.
    hawkes_ll = log_likelihood_on_interval(
        times, params.mu, params.alpha, params.beta, split, T
    )
    poisson_rate = len(train) / split if split > 0 else 0.0
    poisson_ll = poisson_log_likelihood_on_interval(times, poisson_rate, split, T)
    n_test = int(np.sum(times > split))

    try:
        diag = check_fit(train, params)
        ks_p, lb_p, passes = diag.ks.p_value, diag.ljung_box.p_value, diag.passes
    except (ValueError, RuntimeError):
        ks_p = lb_p = float("nan")
        passes = False

    return {
        "n_events": len(times),
        "n_train": len(train),
        "n_test": n_test,
        "branching_ratio": params.branching_ratio,
        "mu": params.mu,
        "alpha": params.alpha,
        "beta": params.beta,
        "half_life": params.excitation_half_life,
        "se_alpha": params.std_errors.get("alpha"),
        "se_beta": params.std_errors.get("beta"),
        "ks_p": ks_p,
        "ljung_box_p": lb_p,
        "diagnostics_pass": passes,
        "holdout_hawkes_ll": hawkes_ll,
        "holdout_poisson_ll": poisson_ll,
        "holdout_ll_gain": hawkes_ll - poisson_ll,
        "holdout_ll_gain_per_event": (
            (hawkes_ll - poisson_ll) / n_test if n_test else float("nan")
        ),
    }


def fit_series(
    times: np.ndarray,
    window_events: int,
    holdout: float,
    period: float,
    bins: int,
    deseason: bool,
) -> dict:
    """Fit a market's arrivals, optionally deseasonalised, in windows."""
    t0 = float(times[0])
    rel = times - t0
    T = float(rel[-1])
    if T <= 0:
        return {"windows": [], "note": "zero-length series"}

    profile_note = "naive (no seasonality correction)"
    if deseason:
        rel, T, profile = deseasonalise(rel, T, period=period, n_bins=bins)
        profile_note = profile.describe()

    if window_events and len(rel) > window_events:
        edges = list(range(0, len(rel), window_events))
        chunks = [rel[a : a + window_events] for a in edges]
        chunks = [c for c in chunks if len(c) >= 50]
    else:
        chunks = [rel]

    results = []
    for chunk in chunks:
        # Each window is re-based to its own origin. A window starting at
        # t=80 days with a baseline fitted from zero would attribute 80 days of
        # absent history to the baseline.
        local = chunk - chunk[0]
        local_T = float(local[-1])
        out = fit_one_window(local, local_T, holdout)
        if out:
            results.append(out)

    return {"windows": results, "note": profile_note}


def summarise(windows: list[dict], key: str) -> dict:
    vals = np.array([w[key] for w in windows if np.isfinite(w.get(key, np.nan))])
    if not len(vals):
        return {}
    return {
        "n": len(vals),
        "median": float(np.median(vals)),
        "mean": float(vals.mean()),
        "q25": float(np.percentile(vals, 25)),
        "q75": float(np.percentile(vals, 75)),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--data", type=Path, default=Path("./data"))
    p.add_argument("--ticker", action="append", default=[], help="restrict; repeatable")
    p.add_argument("--min-trades", type=int, default=2000,
                   help="skip markets thinner than this (validation showed clean "
                        "recovery at ~2,000 events)")
    p.add_argument("--window-events", type=int, default=5000,
                   help="events per fitting window; 0 fits the whole series at once")
    p.add_argument("--holdout", type=float, default=0.25,
                   help="tail fraction of each window held out for the Poisson test")
    p.add_argument("--period", type=float, default=SECONDS_PER_DAY,
                   help="seasonality period in seconds (default 1 day)")
    p.add_argument("--bins", type=int, default=24, help="phase bins per period")
    p.add_argument(
        "--merge-window",
        type=float,
        default=0.001,
        help=(
            "seconds within which trade prints are treated as ONE order "
            "arrival. A single aggressive order matching several resting "
            "orders emits several prints at the same instant; fitting those as "
            "separate arrivals drives beta to infinity and every window pins "
            "the bound. 0 merges only exact ties; negative disables merging"
        ),
    )
    p.add_argument(
        "--ties",
        action="store_true",
        help="report the gap structure per market and exit, without fitting",
    )
    p.add_argument("--out", type=Path,
                   default=Path("./results/diffusion/hawkes_fits.json"))
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    print(f"loading trades from {args.data / 'raw'} ...")
    by_market = load_by_market(args.data)
    wanted = {
        t: v
        for t, v in by_market.items()
        if len(v) >= args.min_trades and (not args.ticker or t in args.ticker)
    }
    if not wanted:
        print(f"no market has >= {args.min_trades:,} trades")
        return 1

    print(f"{len(wanted)} market(s) of {len(by_market)} clear {args.min_trades:,} trades\n")

    if args.ties:
        print("GAP STRUCTURE (choose --merge-window from this)\n")
        for ticker in sorted(wanted, key=lambda t: -len(wanted[t])):
            print(f"{ticker}  ({len(wanted[ticker]):,} prints)")
            print(format_tie_report(tie_report(wanted[ticker])))
        print("\nAn exact-tie fraction above a percent or two means the raw")
        print("series cannot be fitted directly, whatever the optimiser says.")
        return 0

    if args.merge_window >= 0:
        merged = {}
        rates = []
        for ticker, times in wanted.items():
            agg = aggregate_simultaneous(times, tolerance=args.merge_window)
            merged[ticker] = agg.times
            rates.append(agg.merge_rate)
        if rates:
            print(
                f"aggregated prints into orders at {args.merge_window:g}s: "
                f"median {np.median(rates):.1%} merged, "
                f"max {np.max(rates):.1%}"
            )
        # Re-apply the threshold to ORDERS, not prints. Merging removed a third
        # to two thirds of the records, so a market that cleared 2,000 prints
        # can sit well under the event count the recovery study actually
        # supports - one fell to 630. Filtering before the merge quietly admits
        # fits the validation does not cover.
        thin = {t: len(v) for t, v in merged.items() if len(v) < args.min_trades}
        if thin:
            print(
                f"  {len(thin)} market(s) fell below {args.min_trades:,} ORDERS "
                f"after merging and are excluded: "
                + ", ".join(f"{t} ({n:,})" for t, n in sorted(thin.items(), key=lambda kv: kv[1])[:6])
                + ("..." if len(thin) > 6 else "")
            )
        wanted = {
            t: v for t, v in merged.items()
            if len(v) >= max(args.min_trades, 50)
        }
        print()
        if not wanted:
            print(f"nothing clears {args.min_trades:,} orders after merging. "
                  f"Lower --min-trades if you accept the wider intervals.")
            return 1
    print(f"{'market':<38}{'events':>8}{'naive n':>10}{'deseas n':>10}"
          f"{'shrink':>9}{'half-life':>11}{'vs Pois':>10}{'diag':>6}")
    print("-" * 102)

    results = {}
    for ticker in sorted(wanted, key=lambda t: -len(wanted[t])):
        times = wanted[ticker]
        naive = fit_series(times, args.window_events, args.holdout,
                           args.period, args.bins, deseason=False)
        deseas = fit_series(times, args.window_events, args.holdout,
                            args.period, args.bins, deseason=True)

        n_naive = summarise(naive["windows"], "branching_ratio")
        n_deseas = summarise(deseas["windows"], "branching_ratio")
        if not n_naive or not n_deseas:
            print(f"{ticker:<38}{len(times):>8,}   no converged windows")
            continue

        hl = summarise(deseas["windows"], "half_life")
        gain = summarise(deseas["windows"], "holdout_ll_gain_per_event")
        passes = sum(1 for w in deseas["windows"] if w["diagnostics_pass"])

        shrink = n_naive["median"] - n_deseas["median"]
        print(
            f"{ticker:<38}{len(times):>8,}{n_naive['median']:>10.3f}"
            f"{n_deseas['median']:>10.3f}{shrink:>+9.3f}"
            f"{hl.get('median', float('nan')):>10.0f}s"
            f"{gain.get('median', float('nan')):>10.3f}"
            f"{passes:>4}/{len(deseas['windows'])}"
        )

        results[ticker] = {
            "n_events": int(len(times)),
            "naive": {"summary": n_naive, "windows": naive["windows"],
                      "note": naive["note"]},
            "deseasonalised": {"summary": n_deseas, "windows": deseas["windows"],
                               "note": deseas["note"]},
            "half_life_seconds": hl,
            "holdout_ll_gain_per_event": gain,
            "windows_passing_diagnostics": passes,
            "windows_total": len(deseas["windows"]),
        }

    if not results:
        print("\nnothing converged")
        return 1

    naive_all = [r["naive"]["summary"]["median"] for r in results.values()]
    deseas_all = [r["deseasonalised"]["summary"]["median"] for r in results.values()]
    # Median of the per-market DIFFERENCES, not the difference of the medians.
    # Those are different statistics and the second one lied: on the first live
    # run it reported +0.145 where the median per-market shrink was +0.004, a
    # factor of 35, because the two medians came from different markets.
    shrinks = [n - d for n, d in zip(naive_all, deseas_all)]
    gains = [
        r["holdout_ll_gain_per_event"].get("median", float("nan"))
        for r in results.values()
    ]
    gains = [g for g in gains if np.isfinite(g)]
    beat = sum(1 for g in gains if g > 0)

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"  markets fitted            {len(results)}")
    print(f"  naive branching ratio     median {np.median(naive_all):.3f}")
    print(f"  deseasonalised            median {np.median(deseas_all):.3f}")
    print(f"  median per-market shrink  {np.median(shrinks):+.3f}"
          f"   (range {min(shrinks):+.3f} to {max(shrinks):+.3f})")
    print(f"  beats Poisson held-out    {beat}/{len(gains)} markets")
    print()
    print("  Read the shrinkage first. The seasonality study showed a")
    print("  constant-baseline fit inventing n = 0.79 from a varying rate with")
    print("  no self-excitation at all, so the deseasonalised column is the one")
    print("  that can be quoted. The noise floor from the Poisson negative")
    print("  control is 0.19 - anything below that is not evidence.")
    print()
    print("  A market that does not beat Poisson out of sample has not")
    print("  demonstrated self-excitation, whatever its branching ratio says.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "min_trades": args.min_trades,
            "window_events": args.window_events,
            "holdout": args.holdout,
            "period_seconds": args.period,
            "bins": args.bins,
        },
        "noise_floor_from_negative_control": 0.19,
        "median_per_market_shrink": float(np.median(shrinks)),
        "markets": results,
    }, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

```


==============================================================================
# SOURCE: scripts/hawkes_recovery.py — validation studies, add A-D here

`scripts/hawkes_recovery.py`
==============================================================================

```python
#!/usr/bin/env python3
"""Validate the Hawkes estimator before it touches real data.

A Hawkes fit on market data will produce a branching ratio whatever you feed it.
The number is worthless until three questions have been answered about the
estimator itself, and each is a study here:

1. **Does it recover what it generated?** Fit simulated samples with known
   parameters and check both the point estimates and the coverage of the
   confidence intervals. Coverage is the part usually skipped: point estimates
   can look fine while the standard errors are badly wrong, and it is the
   standard errors that decide whether a branching ratio of 0.62 is meaningfully
   different from 0.55.

2. **Does it find excitation where there is none?** Fit a homogeneous Poisson
   process. The estimated branching ratio must be near zero. An estimator that
   reports self-excitation in independent arrivals would invalidate every result
   downstream of it.

3. **Does a time-varying baseline masquerade as self-excitation?** Fit a
   *purely Poisson* process whose rate varies over the day. There is no
   self-excitation at all, but a constant-baseline Hawkes has no other way to
   explain clustering and absorbs the seasonality as spurious excitation.

Study 3 is the one that matters for the Kalshi application. Published
order-flow branching ratios near 0.9 are contested precisely on these grounds -
Hardiman, Bercot and Bouchaud argue that estimates approaching criticality are
partly an artefact of non-stationary baselines and kernel misspecification
rather than genuine reflexivity. Kalshi trade flow has obvious intraday and
event-driven seasonality, so quoting a branching ratio without addressing this
would reproduce the exact error the literature has already litigated.

    python scripts/hawkes_recovery.py                 # all three, 100 replications
    python scripts/hawkes_recovery.py --reps 500      # tighter coverage estimates
    python scripts/hawkes_recovery.py --study 3       # just the seasonality study
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.diffusion.hawkes.baseline import deseasonalise  # noqa: E402
from quant.diffusion.hawkes.diagnostics import check_fit  # noqa: E402
from quant.diffusion.hawkes.model import fit  # noqa: E402
from quant.diffusion.hawkes.simulation import simulate_cluster  # noqa: E402

log = logging.getLogger("recovery")


def simulate_poisson(rate: float, T: float, seed: int) -> np.ndarray:
    """Homogeneous Poisson arrivals on ``[0, T]``."""
    rng = np.random.default_rng(seed)
    return np.sort(rng.uniform(0.0, T, size=rng.poisson(rate * T)))


def simulate_seasonal_poisson(
    base_rate: float, amplitude: float, period: float, T: float, seed: int
) -> np.ndarray:
    """Inhomogeneous Poisson with a sinusoidal rate, by thinning.

    ``lambda(t) = base_rate * (1 + amplitude * sin(2*pi*t / period))``. There is
    no self-excitation whatsoever: every arrival is independent given the rate
    function. Any branching ratio a Hawkes fit reports on this data is entirely
    an artefact of forcing a constant baseline onto a varying one.
    """
    rng = np.random.default_rng(seed)
    ceiling = base_rate * (1.0 + amplitude)
    candidates = np.sort(rng.uniform(0.0, T, size=rng.poisson(ceiling * T)))
    intensity = base_rate * (1.0 + amplitude * np.sin(2 * np.pi * candidates / period))
    return candidates[rng.uniform(0.0, ceiling, size=len(candidates)) <= intensity]


def study_recovery(reps: int, T: float, truth: dict[str, float]) -> dict:
    """Study 1: parameter recovery and confidence-interval coverage."""
    mu, alpha, beta = truth["mu"], truth["alpha"], truth["beta"]
    true_n = alpha / beta
    print(f"\n{'=' * 72}\nSTUDY 1  Parameter recovery and CI coverage")
    print(f"  truth: mu={mu} alpha={alpha} beta={beta}  n={true_n:.3f}")
    print(f"  {reps} replications, window T={T:g}\n{'=' * 72}")

    rows, excluded = [], 0
    for rep in range(reps):
        times, _ = simulate_cluster(mu, alpha, beta, T, seed=1000 + rep)
        if len(times) < 50:
            continue
        try:
            p = fit(times, T=T, compute_std_errors=True)
        except (RuntimeError, ValueError) as exc:
            log.warning("rep %d failed: %s", rep, exc)
            excluded += 1
            continue
        if not p.converged:
            # A boundary or non-converged fit is excluded and counted, never
            # averaged in. Silently including them is what turned a broken
            # optimiser into a plausible-looking mean on the first run.
            excluded += 1
            continue
        rows.append(
            {
                "mu": p.mu,
                "alpha": p.alpha,
                "beta": p.beta,
                "n": p.branching_ratio,
                "se_mu": p.std_errors.get("mu"),
                "se_alpha": p.std_errors.get("alpha"),
                "se_beta": p.std_errors.get("beta"),
                "n_events": p.n_events,
            }
        )
        if (rep + 1) % 20 == 0:
            print(f"  ... {rep + 1}/{reps}")

    if not rows:
        raise RuntimeError("every replication failed")

    print(f"\n  {len(rows)} converged fits, {excluded} excluded "
          f"(non-convergence or boundary), "
          f"median {np.median([r['n_events'] for r in rows]):.0f} events each\n")
    print(f"  {'param':<8}{'truth':>10}{'mean est':>12}{'bias %':>10}"
          f"{'mean SE':>10}{'emp SD':>10}{'95% cov':>10}")
    print("  " + "-" * 60)

    summary = {}
    for name, true_value in (("mu", mu), ("alpha", alpha), ("beta", beta)):
        estimates = np.array([r[name] for r in rows])
        ses = np.array(
            [r[f"se_{name}"] for r in rows if r[f"se_{name}"] is not None]
        )
        covered = [
            abs(r[name] - true_value) <= 1.96 * r[f"se_{name}"]
            for r in rows
            if r[f"se_{name}"] is not None
        ]
        coverage = float(np.mean(covered)) if covered else float("nan")
        bias_pct = 100.0 * (estimates.mean() - true_value) / true_value
        print(
            f"  {name:<8}{true_value:>10.4f}{estimates.mean():>12.4f}"
            f"{bias_pct:>+10.2f}{ses.mean() if len(ses) else np.nan:>10.4f}"
            f"{estimates.std(ddof=1):>10.4f}{coverage:>10.1%}"
        )
        summary[name] = {
            "truth": true_value,
            "mean_estimate": float(estimates.mean()),
            "bias_pct": float(bias_pct),
            "mean_std_error": float(ses.mean()) if len(ses) else None,
            "empirical_sd": float(estimates.std(ddof=1)),
            "ci_coverage_95": coverage,
        }

    n_hat = np.array([r["n"] for r in rows])
    print(f"\n  branching ratio: truth {true_n:.4f}, "
          f"mean {n_hat.mean():.4f}, SD {n_hat.std(ddof=1):.4f}")
    print("\n  Read the last two columns together. A mean standard error close to")
    print("  the empirical SD, and coverage near 95%, is what licenses quoting a")
    print("  confidence interval on real data. Coverage far below 95% means the")
    print("  point estimates may be fine while the error bars are fiction.")

    summary["branching_ratio"] = {
        "truth": true_n,
        "mean_estimate": float(n_hat.mean()),
        "empirical_sd": float(n_hat.std(ddof=1)),
    }
    summary["n_replications"] = len(rows)
    summary["n_excluded"] = excluded
    return summary


def study_negative_control(reps: int, T: float, rate: float) -> dict:
    """Study 2: Poisson data must not produce self-excitation."""
    print(f"\n{'=' * 72}\nSTUDY 2  Negative control - homogeneous Poisson")
    print(f"  truth: n = 0 exactly. {reps} replications at rate {rate}, T={T:g}")
    print(f"{'=' * 72}")

    ratios, excluded = [], 0
    for rep in range(min(reps, 50)):
        times = simulate_poisson(rate, T, seed=5000 + rep)
        if len(times) < 50:
            continue
        try:
            p = fit(times, T=T, compute_std_errors=False)
        except (RuntimeError, ValueError):
            excluded += 1
            continue
        if not p.converged:
            excluded += 1
            continue
        ratios.append(p.branching_ratio)

    ratios = np.array(ratios)
    print(f"\n  {len(ratios)} converged fits on independent arrivals "
          f"({excluded} excluded)")
    print(f"  branching ratio: mean {ratios.mean():.4f}, "
          f"median {np.median(ratios):.4f}, 95th pct {np.percentile(ratios, 95):.4f}")
    verdict = "PASS" if np.median(ratios) < 0.15 else "FAIL"
    print(f"  [{verdict}] a median near zero means the estimator does not "
          f"manufacture excitation")
    print("\n  The 95th percentile is the number to remember: it is roughly the")
    print("  threshold a real branching ratio must clear before it is evidence of")
    print("  anything beyond finite-sample noise.")

    return {
        "n_fits": len(ratios),
        "n_excluded": excluded,
        "mean_branching_ratio": float(ratios.mean()),
        "median_branching_ratio": float(np.median(ratios)),
        "p95_branching_ratio": float(np.percentile(ratios, 95)),
        "verdict": verdict,
    }


def study_seasonality(reps: int, T: float, rate: float, amplitude: float) -> dict:
    """Study 3: a varying baseline read as self-excitation."""
    period = T / 20.0
    print(f"\n{'=' * 72}\nSTUDY 3  Seasonality contamination")
    print(f"  Inhomogeneous Poisson, rate {rate} * (1 + {amplitude}*sin), "
          f"period {period:g}")
    print(f"  truth: n = 0 exactly. There is no self-excitation in this data.")
    print(f"{'=' * 72}")

    ratios, rejected, excluded = [], 0, 0
    for rep in range(min(reps, 50)):
        times = simulate_seasonal_poisson(rate, amplitude, period, T, seed=9000 + rep)
        if len(times) < 50:
            continue
        try:
            p = fit(times, T=T, compute_std_errors=False)
            if not p.converged:
                excluded += 1
                continue
            diag = check_fit(times, p)
        except (RuntimeError, ValueError):
            excluded += 1
            continue
        ratios.append(p.branching_ratio)
        rejected += int(not diag.passes)

    ratios = np.array(ratios)
    print(f"\n  {len(ratios)} converged fits on data with zero true "
          f"self-excitation ({excluded} excluded)")
    print(f"  branching ratio: mean {ratios.mean():.4f}, "
          f"median {np.median(ratios):.4f}, max {ratios.max():.4f}")
    print(f"  residual diagnostics rejected the fit in "
          f"{rejected}/{len(ratios)} cases ({rejected / max(len(ratios), 1):.0%})")
    print("\n  Two things to take from this:")
    print("  1. A constant-baseline Hawkes reports self-excitation that is not")
    print("     there, because absorbing a varying rate is the only way it can")
    print("     explain the clustering. Kalshi flow is strongly seasonal, so any")
    print("     branching ratio fitted on raw trade times inherits this bias.")
    print("  2. Whether the diagnostics catch it is the practical question, and")
    print("     it is the reason both tests are run rather than KS alone.")
    print("\n  The mitigation, applied in the real fit: estimate a time-varying")
    print("  baseline first, or fit within windows short enough that the baseline")
    print("  is locally flat, and report the branching ratio both ways.")

    return {
        "n_fits": len(ratios),
        "n_excluded": excluded,
        "true_branching_ratio": 0.0,
        "mean_branching_ratio": float(ratios.mean()),
        "median_branching_ratio": float(np.median(ratios)),
        "max_branching_ratio": float(ratios.max()),
        "diagnostics_rejection_rate": rejected / max(len(ratios), 1),
    }


def study_correction(reps: int, T: float, rate: float, amplitude: float) -> dict:
    """Study 4: does the seasonal time change actually fix study 3?

    Two halves, and the second matters more than the first. Removing a spurious
    branching ratio is easy if you are willing to shrink everything; the useful
    question is whether the correction is *specific* - whether genuine
    self-excitation survives it intact.
    """
    period = T / 20.0
    print(f"\n{'=' * 72}\nSTUDY 4  Does the seasonal time change fix study 3?")
    print(f"{'=' * 72}")

    print("\n  (a) seasonal Poisson, true n = 0")
    print(f"      {'naive':>12}{'corrected':>12}{'peak/trough':>14}")
    naive_a, corr_a = [], []
    for rep in range(min(reps, 8)):
        times = simulate_seasonal_poisson(rate, amplitude, period, T, seed=9000 + rep)
        if len(times) < 200:
            continue
        try:
            n_raw = fit(times, T=T, compute_std_errors=False)
            ops, ops_T, prof = deseasonalise(times, T, period=period, n_bins=20)
            n_fix = fit(ops, T=ops_T, compute_std_errors=False)
        except (RuntimeError, ValueError):
            continue
        naive_a.append(n_raw.branching_ratio)
        corr_a.append(n_fix.branching_ratio)
        print(f"      {n_raw.branching_ratio:>12.3f}{n_fix.branching_ratio:>12.3f}"
              f"{prof.peak_to_trough:>14.2f}")

    print("\n  (b) genuine Hawkes, no seasonality, true n = 0.5")
    print(f"      {'naive':>12}{'corrected':>12}")
    naive_b, corr_b = [], []
    for rep in range(min(reps, 6)):
        times, _ = simulate_cluster(0.5, 0.8, 1.6, T, seed=400 + rep)
        try:
            n_raw = fit(times, T=T, compute_std_errors=False)
            ops, ops_T, _ = deseasonalise(times, T, period=period, n_bins=20)
            n_fix = fit(ops, T=ops_T, compute_std_errors=False)
        except (RuntimeError, ValueError):
            continue
        naive_b.append(n_raw.branching_ratio)
        corr_b.append(n_fix.branching_ratio)
        print(f"      {n_raw.branching_ratio:>12.3f}{n_fix.branching_ratio:>12.3f}")

    if not naive_a or not naive_b:
        return {"error": "no converged fits"}

    ma, mca = float(np.median(naive_a)), float(np.median(corr_a))
    mb, mcb = float(np.median(naive_b)), float(np.median(corr_b))
    verdict = "PASS" if mca < 0.19 and abs(mcb - 0.5) < 0.1 else "FAIL"

    print(f"\n  spurious n removed:   {ma:.3f} -> {mca:.3f}   (truth 0)")
    print(f"  genuine n preserved:  {mb:.3f} -> {mcb:.3f}   (truth 0.5)")
    print(f"  cost of the correction on real excitation: {mb - mcb:+.3f}")
    print(f"\n  [{verdict}] the correction is specific to seasonality rather")
    print("  than shrinking every branching ratio it touches. That specificity")
    print("  is what licenses quoting the corrected number on real data.")

    return {
        "spurious_naive": ma,
        "spurious_corrected": mca,
        "genuine_naive": mb,
        "genuine_corrected": mcb,
        "cost_on_real_excitation": mb - mcb,
        "verdict": verdict,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--reps", type=int, default=100, help="replications per study")
    p.add_argument("--window", type=float, default=2000.0, help="observation window")
    p.add_argument(
        "--study",
        type=int,
        action="append",
        default=[],
        choices=[1, 2, 3, 4],
        help="run only these studies; repeatable",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("./results/diffusion/hawkes_validation.json"),
        help="where to write the machine-readable summary",
    )
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    studies = args.study or [1, 2, 3, 4]

    results: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "replications": args.reps,
        "window": args.window,
    }

    if 1 in studies:
        results["recovery"] = study_recovery(
            args.reps, args.window, {"mu": 0.5, "alpha": 0.8, "beta": 1.6}
        )
    if 2 in studies:
        results["negative_control"] = study_negative_control(
            args.reps, args.window, rate=1.0
        )
    if 3 in studies:
        results["seasonality"] = study_seasonality(
            args.reps, args.window, rate=1.0, amplitude=0.8
        )

    if 4 in studies:
        results["correction"] = study_correction(
            args.reps, args.window, rate=1.0, amplitude=0.8
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

```


==============================================================================
# SOURCE: HawkesParameters (excerpt from schema.py)

The frozen result type every fit returns. Derived quantities are
properties, not stored fields, so they cannot drift from the parameters.
==============================================================================

```python
class HawkesParameters(BaseModel):
    """Fitted parameters of a univariate Hawkes process with exponential kernel.

    The conditional intensity is

        lambda(t) = mu + sum_{t_i < t} alpha * exp(-beta * (t - t_i))

    where

        mu    baseline intensity, events per unit time
        alpha excitation amplitude, the jump in intensity caused by one event
        beta  exponential decay rate of that excitation

    The branching ratio is the integral of the kernel,

        n = int_0^inf alpha * exp(-beta * s) ds = alpha / beta

    and is the expected number of direct offspring per event. The process is
    stationary if and only if n < 1. Note that mu does not enter the branching
    ratio at all: the baseline controls how many immigrant events arrive, not
    how strongly each event excites the next.

    Branching ratio and half-life are computed from alpha and beta rather than
    stored, so they cannot disagree with the fitted parameters.
    """

    mu: float = Field(..., gt=0, description="Baseline intensity, events per time_unit")
    alpha: float = Field(..., gt=0, description="Excitation amplitude, per time_unit")
    beta: float = Field(..., gt=0, description="Kernel decay rate, per time_unit")
    time_unit: Literal["second", "minute", "hour", "day"] = Field(
        ..., description="Unit that mu, alpha and beta are expressed in"
    )
    log_likelihood: float = Field(..., description="Maximised log-likelihood")
    n_events: int = Field(..., gt=0, description="Events used in the fit")
    observation_window: float = Field(
        ..., gt=0, description="Length of the observation window in time_unit"
    )
    converged: bool = Field(..., description="Whether the optimiser reported success")
    std_errors: dict[str, float] = Field(
        default_factory=dict,
        description="Asymptotic standard errors keyed by parameter name",
    )

    model_config = FROZEN

    @property
    def branching_ratio(self) -> float:
        """Expected direct offspring per event, ``alpha / beta``."""
        return self.alpha / self.beta

    @property
    def is_stationary(self) -> bool:
        """A branching ratio at or above one implies an explosive process."""
        return self.branching_ratio < 1.0

    @property
    def excitation_half_life(self) -> float:
        """Time for excitation to decay by half, ``ln(2) / beta``, in time_unit."""
        return math.log(2.0) / self.beta

    @property
    def expected_cluster_size(self) -> Optional[float]:
        """Mean total events triggered by one immigrant, ``1 / (1 - n)``.

        Undefined for a non-stationary fit.
        """
        if not self.is_stationary:
            return None
        return 1.0 / (1.0 - self.branching_ratio)

    # Deliberately not rejected: a non-stationary fit is a diagnostic result,
    # not a validation failure. Suppressing it would hide the most informative
    # thing the fit can tell you about a badly specified model or a bad window.
```
