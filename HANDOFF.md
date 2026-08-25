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

---

## Working with two models

This project is worked by a strong general model on a limited budget and a
capable local model with none. The split is not about which is smarter; it is
about **which mistakes each kind of task can catch on its own.**

| Delegate freely | Keep for careful review |
|---|---|
| Implementation from a written spec | Deciding what to build and why |
| Writing tests | Interpreting an ambiguous result |
| Refactoring, plumbing, CLI work | Judging whether a number is defensible |
| Running jobs and reporting output | Diagnosing a failure no spec anticipated |
| Data wrangling and conversion | Write-ups a research panel will read |

**The rule: code has tests, interpretation has nothing.**

A wrong implementation fails loudly against known-answer data — which is why
every estimator here ships with a validation study, and why
`docs/power_law_spec.md` specifies its four studies before any code. That
harness *is* the acceptance test, and it makes implementation safe to hand off.

A wrong interpretation fails silently. Every serious error in this project so
far has been a **plausible-looking wrong answer that reported success**:

- `alpha = 1e190` with `converged=True` — invisible at 20,000 events
- `n = 0.664` against a truth of 0.500, with `converged=True`, from fitting raw
  trade prints
- a reported "shrinkage" of +0.145 that was +0.004 under the right definition

None were caught by code failing. All were caught by someone reading a number
and finding it implausible.

So: implement wherever is cheapest, but **every number destined for a write-up
gets read by the model you trust most, and every claim gets traced back to the
code or data that produced it.**

Two tasks specifically worth not delegating: verifying the YES/NO order book
complementarity (a judgement about whether an assumption holds, with no test to
fall back on), and `docs/results_summary.md`.

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
