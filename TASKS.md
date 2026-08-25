# Tasks

Goal: two defensible portfolio projects for quant researcher roles, no PhD.
Scope doc written 2026-08-21. Today is 2026-08-25 — day 5 of a 6-week plan.

## Status

**Infrastructure is finished.** Collection runs on a server under systemd, the
panel is pinned and verified balanced, both projects' data is in hand, and both
methodology documents are written. Nothing on the critical path is waiting on
setup any more.

**The next phase is analysis, and the two projects are in opposite positions:**

- **Project 2 can produce real results today.** All its data is already
  downloaded — 151,477 slow-arm trades over 89 days, 3.56M fast-arm trades.
- **Project 1 needs the collector to run.** Its result is a *time series*
  question — does the short gap ever cross zero, and for how long? — and there
  are only a few hours of series so far.

So: work Project 2 now while Project 1's data accrues. That is the whole
sequencing decision.

## Live now

- Oracle server `159.54.168.0`, systemd + data-freshness watchdog
- 150 pinned markets, 60s full-depth snapshots, since 2026-08-25 00:53 UTC
- Verified: 150/150 markets full-span, zero outages, median and p95 60s
- `ssh -i $env:USERPROFILE\.ssh\oracle_key ubuntu@159.54.168.0`

## Do next, in order

- [ ] **Deseasonalised Hawkes fit — slow arm.** 13 markets with 2,000+ trades.
      Finding 6 rules out the naive fit, so this needs a time-varying baseline
      or short local windows, reported both ways. **Project 2's first real
      result, and every input already exists on disk.**
- [ ] **Hawkes fit — fast arm.** 3.56M events over 9h13m. The contrast with the
      slow arm is the contribution, not either fit alone.
- [ ] **Poisson baseline on held-out likelihood.** A Hawkes fit that does not
      beat Poisson has demonstrated nothing. Required before any fit is
      reportable, and named as outstanding in the write-up.
- [ ] **Verify YES/NO complementarity.** Previously filed as blocked. It is not
      — the raw payloads are on disk and the check is an afternoon. It is
      load-bearing for every price in Project 1 and is currently a stated
      limitation in the methodology doc.
- [ ] **Bucket-sum detector, short direction.** Write it now, run it in a week
      when there is enough series to say something.

## Findings — complete, and in the write-ups

- [x] **Fee ceiling.** The taker fee rounds up to a whole cent per leg against a
      fixed 100¢ payout, so an N-leg basket owes at least N cents. Past ~10 legs
      no dislocation clears it. Arithmetic, not empirics.
- [x] **Direction asymmetry.** Short needs only mutual exclusivity, which the
      exchange flags. Long needs collective exhaustiveness, which it does not
      certify and prices cannot settle. Only the short side is identifiable.
- [x] **Long direction closed at the touch.** Every family sampled had an ask
      sum of 100.4–109.2¢.
- [x] **Large-field overround is structural.** The minimum tick props up every
      longshot; 243¢ across 293 families in one run.
- [x] **Capacity is not volume.** 368,438 in volume, one contract at the ask.
- [x] **Seasonality fabricates reflexivity.** Zero-self-excitation data yields a
      fitted branching ratio of 0.79, and the diagnostics catch it 12% of the
      time.
- [x] **Noise floor 0.19.** The 95th-percentile branching ratio on Poisson data.
      Any real estimate must clear it to mean anything.
- [x] **Panel balance fails two ways.** Volume ranking churns composition across
      restarts *and* fills the universe with same-day contracts. Both were
      invisible in a log reporting "0 errors".
- [x] **Two regimes.** 8 trades/hour on the busiest pinned market against
      387,000/hour exchange-wide — a factor of 48,000. The projects want
      opposite data, and both datasets already exist.
- [x] **Taker imbalance 63/37 buy/sell**, in YES terms.

## Project 1 — Kalshi

- [ ] **Bucket-sum detector** — short direction first, it is the identifiable one
- [ ] **Monotonicity detector** — nested threshold families
- [ ] **Backtest engine** — chronological replay, no lookahead, realistic fills
- [ ] **Fractional Kelly sizing** — capped by observed book depth
- [ ] **Fair-value model** — ONE domain. Note the long-dated panel yields few
      settled outcomes, so calibration needs a deliberately different sample
- [ ] **Calibration** — Brier with decomposition, log loss, reliability, skill

## Project 2 — Diffusion

- [x] ~~Hawkes simulation, MLE, time-rescaling diagnostics~~ (08-24)
- [x] ~~Validation: recovery, negative control, seasonality~~ (08-24)
- [x] ~~Trades backfilled, both arms~~ (08-25)
- [ ] **Deseasonalised fit** — see "Do next"
- [ ] **Poisson baseline comparison**
- [ ] **Bivariate Hawkes** — buy vs sell flow with cross-excitation. The taker
      side is already parsed and tested, so this is modelling, not data work
- [ ] **Power-law kernel robustness check** — the literature's critique
      implicates kernel misspecification directly. Named in the write-up as
      outstanding work, not optional polish
- [ ] **Event alignment** — Fed, CPI, jobs. Announcement times are exogenous
- [ ] **Price-discovery link** — event contracts have a known terminal value, so
      convergence is measurable without a benchmark model to argue about
- [ ] **Reddit as a second process** — if approval ever lands. Optional upside

## Write-ups

- [x] ~~docs/kalshi_methodology.md~~ (08-25) — 331 lines
- [x] ~~docs/diffusion_methodology.md~~ (08-25) — 347 lines
- [ ] **docs/results_summary.md** — one page. Write it after the first fits
      exist; right now it would only restate the methodology documents

## Blocked

- [ ] **Reconcile the fee model against a settled fill** — needs a real trade.
      Rounding granularity and the per-series multiplier (0–2) are unverified,
      and every number in the fee section inherits this
- [ ] **Reddit API approval** — submitted 08-23, no ETA, no longer blocking

## Watch out for

- **Quoting a branching ratio without deseasonalising.** Our own simulation
  produces 0.79 out of nothing. The most attackable thing available.
- **Concatenating the two order book panels.** The laptop data (08-23 → 08-24)
  used a different universe. Two datasets, not one series.
- **Scope creep on fair value.** One domain, finished, beats three started.
- **Trusting the log over the files.** "0 errors" was true and useless.
- **A finding that says "no edge" is still a finding.** The fee ceiling is
  exactly that, and it is worth more than a curve-fit backtest.

## Done

- [x] ~~Architecture, Pydantic schema, fee model, REST client~~ (08-23)
- [x] ~~Corrected Hawkes branching ratio~~ (08-23) — was `α*β/λ₀`, is `α/β`
- [x] ~~Selection layer and payload parser fix~~ (08-23) — documented field
      names returned zero rows against production
- [x] ~~Collection running~~ (08-23)
- [x] ~~Published to GitHub~~ (08-23) — secrets confirmed ignored
- [x] ~~Project 2 unblocked~~ (08-24) — trades turned out to be backfillable
- [x] ~~Hawkes estimator and validation studies~~ (08-24)
- [x] ~~Caught an optimiser runaway~~ (08-24) — α ≈ 1e190 reported as success,
      invisible at 20,000 events, fatal at 2,000
- [x] ~~Coverage audit, universe pinning, expiry filter~~ (08-24/25)
- [x] ~~**Collector on a server**~~ (08-25) — systemd, watchdog, verified clean
- [x] ~~Trades backfilled~~ (08-25) — 151,477 slow arm, 3.56M fast arm
- [x] ~~Both methodology documents~~ (08-25)
- [x] ~~Committed and pushed~~ (08-25)
