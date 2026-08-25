# Tasks

Goal: two defensible portfolio projects for quant researcher roles, no PhD.
Scope doc written 2026-08-21. Today is 2026-08-25 — day 5 of a 6-week plan.

**29 shipped · 24 findings held · 14 open · 2 blocked. 402 tests passing.**

## Status

**Infrastructure is finished** and is off the critical path. Collection runs on a
server under systemd, the panel is pinned and verified balanced, both arms of
data are in hand, and both methodology documents are written.

**Both projects have now produced results, and the most valuable ones are
negative.** Project 2's headline claim — reflexivity in event-contract trade
arrivals — has been substantially dismantled by the project's own controls. That
is the strongest material in the repo: a result found, doubted, tested, and
retracted with the evidence attached.

**Project 1 is the thinner half.** It has findings, a verified price basis, a
working detector and now a market-calibration benchmark, but no backtest and no
*fitted* fair-value model. The temperature model is written and tested and
refuses to quote until its error distribution is measured, which is the honest
state but not a result. That is where the remaining portfolio risk is.

## Do next, in order

- [ ] **Run `scripts/probe_weather.py`** — measures the Kalshi temperature
      payload and the NWS forecast shape. Blocks the pair collector, and can
      invalidate the model outright: if the settlement rules do not name a
      station and a whole degree, the half-degree bucket boundaries are wrong
      on every contract.
- [ ] **Forecast/observation pair collector** — written against the probe's
      measured shapes, not before. ~30 days of daily requests to reach the
      minimum the error model will accept, or an AWS Open Data backfill to get
      there this month.
- [ ] **Fit and score the temperature model** against settled outcomes with
      `quant.common.statistics.calibration`. The question is not whether it
      makes money but whether it is better calibrated than the price.
- [ ] **Scale market calibration to ~10,000 settled markets** — 398 gave 7%
      power against a favourite-longshot bias. One evening of API time.
- [ ] **Backtest engine** — chronological replay, no lookahead, fills capped at
      observed depth. Turns "detected" into "would have made money".
- [ ] **Wire the stationarity p-value into `fit_diffusion.py`** — reported
      beside every `n`, as KS and Ljung-Box already are. A flagged window's
      branching ratio prints as uninterpretable rather than being averaged in.
- [ ] **Recompute the bimodality on unflagged windows only** — does the
      0.23 / 0.88 split survive once drift-contaminated windows are excluded?
- [ ] **`docs/results_summary.md`** — now writable, because there are real
      results to summarise.

## Findings — established, with numbers

### Project 2 — diffusion

- [x] **A 2× rate step fabricates a branching ratio of 0.799** on data with zero
      self-excitation; 0.034 with no step. The real high mode is 0.88.
- [x] **The time-rescaling diagnostics rejected 0 of 24** on that fabricated
      fit. They validate the fitted intensity path, not the mechanism, and
      cannot distinguish self-excitation from a time-varying baseline.
      **A diagnostic pass is not validation.**
- [x] **Branching ratio tracks rate drift on the real panel** — Spearman +0.725,
      p = 0.001 over 18 windows; 12 of 18 vary more than their own fitted model
      allows. The two largest swings (5.62×, 4.43×) carry the two largest
      ratios (0.937, 0.904).
- [x] **Power-law misspecification ruled out** (Study C) — reproduces none of
      the three real signatures. Rejection 10% against 72%, no bimodality at any
      ε, and the n/half-life relation has the opposite sign.
- [x] **Power-law kernel noise floor 0.141**, against 0.19 for the exponential.
- [x] **ε is not identified at ~2,400 events** — se(n) ≈ 7%, se(ε) ≈ 22–54%,
      identified in 0 of 12 fits. The result type refuses to report it.
- [x] **The power-law kernel does not invent long memory** (Study D) — recovers
      n to 0.007 on exponential data and pushes ε to its ceiling in 11/12.
- [x] **Seasonality fabricates reflexivity** — zero-self-excitation data yields
      0.79–0.95; diagnostics catch it 12% of the time.
- [x] **Seasonality is exonerated as the driver here** — median per-market
      shrink is +0.000 / −0.0002 / +0.004 across three periods.
- [x] **A third of "trades" are not arrivals** — one aggressive order emits
      several prints at one instant; ~30% are exact ties.
- [x] **Merge tolerance is load-bearing** — at 0.1 s, naive `n` jumps
      0.466 → 0.723 on 0.4% more prints merged.
- [x] **Some fits are capturing microstructure, not diffusion** — a 45 ms
      half-life on a window spanning weeks, and it is the true MLE by 130+
      log-likelihood units. The model is inadequate, not the optimiser.
- [x] **Two regimes** — 8 trades/hour pinned against 387,000/hour exchange-wide.

### Project 1 — Kalshi

- [x] **YES/NO complementarity verified** — 108,489 two-sided books, zero
      crossings, median bid sum 99.0¢. Every price in Project 1 rests on this.
- [x] **Fees killed 5,792 of 5,792** baskets whose bid sum exceeded 100¢.
      Closest miss 0.1¢. The market sits *on* the fee-adjusted bound.
- [x] **Large fields are further from arbitrage than anything else** — 5,395
      families: requirement rises as `100 + N` while bid sums FALL 94.0c ->
      54.0c; median short gap widens 9c -> 140c. Not one family inside.
- [x] **The overround is spread, not mispricing** — summed spread 11.3c at 2
      legs to 486c at 50+, a 43x increase. Minimum tick props up every longshot
      ask; the bid is absent.
- [x] **Fee ceiling** — an N-leg basket owes at least N cents. Arithmetic.
- [x] **Direction asymmetry** — only the short side is identifiable; long needs
      exhaustiveness the exchange does not certify.
- [x] **Long direction closed at the touch** — ask sums 100.4–109.2¢.
- [x] **Large-field overround is structural** — 243¢ across 293 families.
- [x] **Capacity is not volume** — 368,438 in volume, one contract at the ask.
- [x] **36.6% of snapshots have no ask, 30% no book at all** — effective panel
      ~86 of 150.
- [x] **The 98–99¢ markets are untradeable** — no offer exists, and the only
      executable trade has a fee at or above its entire expected value.

## Project 1 — open

- [x] ~~Event-complete universe selection~~ — **cancelled 08-25**, the question
      it existed to answer was settled by one API scan instead
- [ ] **Backtest engine** — chronological replay, realistic fills
- [ ] **Monotonicity detector** — nested threshold families
- [x] **Calibration metrics** — Brier with decomposition, log loss,
      reliability, skill. Shipped with 29 tests.
- [x] **Market calibration measured** — z = +0.20, p = 0.845 at one hour, and
      the power analysis is the finding: 7% against a favourite-longshot bias.
- [~] **Fair-value model — temperature** — model and trade rule written and
      tested; refuses to quote until the forecast error distribution is fitted.
      See `docs/fair_value_temperature.md`.
- [x] **Fractional Kelly sizing** — capped by observed depth, sized at the
      probability moved one standard error against the position

## Project 2 — open

- [x] **Merge sweep above 10 ms** — 99% of the move sits between 30 ms and
      100 ms on 0.30% of events, then flat. Bimodality invariant across the grid.
- [ ] **Stationarity p-value in `fit_diffusion`** — see "Do next"
- [ ] **Bimodality on unflagged windows** — see "Do next"
- [ ] **Fast-arm fit** — 3.56M events
- [ ] **Bivariate Hawkes** — buy vs sell cross-excitation; taker side is parsed
- [ ] **Event alignment** — Fed, CPI, jobs; announcement times are exogenous
- [ ] **Price-discovery link** — terminal value is known, so convergence is
      measurable without a benchmark model to argue about

## Shared

- [ ] **`docs/results_summary.md`** — one page
- [ ] **`.gitattributes`** — three lines; stops `core.autocrlf` handing a cloner
      a diff on every file. Its own commit, not bundled.

## Blocked

- [ ] **Reconcile the fee model against a settled fill** — needs a real trade.
      Rounding granularity and the per-series multiplier are unverified, and
      every number in the fee section inherits this.
- [ ] **Reddit API approval** — submitted 08-23, no ETA, no longer blocking.

## Watch out for

- **Quoting a branching ratio as reflexivity.** Rate drift produces 0.80 from
  nothing and the diagnostics pass on it. Attach the stationarity flag.
- **Treating a diagnostic pass as validation.** Demonstrated false.
- **Reporting ε.** Identified in 0 of 12 fits. Don't route around the type.
- **Concatenating the two order book panels.** Different universes. Two
  datasets, not one series.
- **Presenting the two-leg bucket-sum result as new.** It restates the
  complementarity finding — same quantity, same 99.0¢ median.
- **Scope creep on fair value.** One domain finished beats three started.
- **Trusting the log over the files.** "0 errors" was true and useless.
- **A finding that says "no edge" is still a finding** — and on this repo, the
  negatives are the best material in it.

## Done

- [x] ~~Architecture, Pydantic schema, fee model, REST client~~ (08-23)
- [x] ~~Corrected Hawkes branching ratio~~ (08-23) — was `α*β/λ₀`, is `α/β`
- [x] ~~Selection layer and payload parser fix~~ (08-23)
- [x] ~~Published to GitHub~~ (08-23) — secrets confirmed ignored
- [x] ~~Hawkes estimator and validation studies~~ (08-24)
- [x] ~~Caught an optimiser runaway~~ (08-24) — α ≈ 1e190 reported as success
- [x] ~~Coverage audit, universe pinning, expiry filter~~ (08-24/25)
- [x] ~~Collector on a server~~ (08-25) — systemd, watchdog, verified clean
- [x] ~~Trades backfilled~~ (08-25) — 151,477 slow arm, 3.56M fast arm
- [x] ~~Both methodology documents~~ (08-25)
- [x] ~~Tie merging and preprocessing~~ (08-25)
- [x] ~~Deseasonalised slow-arm fit + held-out Poisson~~ (08-25) — 9/9 beat it
- [x] ~~Power-law kernel rewritten and validated~~ (08-25)
- [x] ~~Studies A, B, C, D~~ (08-25)
- [x] ~~Study E — non-stationarity~~ (08-25)
- [x] ~~Window stationarity test on real data~~ (08-25)
- [x] ~~Preprocessing sensitivity sweep, 12 cells~~ (08-25)
- [x] ~~YES/NO complementarity verified~~ (08-25)
- [x] ~~Bucket-sum detector built and run~~ (08-25)
