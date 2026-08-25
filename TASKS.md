# Tasks

Goal: two defensible portfolio projects for quant researcher roles, no PhD.
Scope doc written 2026-08-21. Today is 2026-08-25 — day 5 of a 6-week plan.

**26 shipped · 22 findings held · 16 open · 2 blocked. 283 tests passing.**

## Status

**Infrastructure is finished** and is off the critical path. Collection runs on a
server under systemd, the panel is pinned and verified balanced, both arms of
data are in hand, and both methodology documents are written.

**Both projects have now produced results, and the most valuable ones are
negative.** Project 2's headline claim — reflexivity in event-contract trade
arrivals — has been substantially dismantled by the project's own controls. That
is the strongest material in the repo: a result found, doubted, tested, and
retracted with the evidence attached.

**Project 1 is the thinner half.** It has findings, a verified price basis and a
working detector, but no backtest, no fair-value model and no calibration. That
is where the remaining portfolio risk is.

## Do next, in order

- [ ] **Event-complete universe selection** (`discovery.py`) — pick whole
      mutually-exclusive families against a leg budget instead of top-N tickers
      by volume. **The highest-value item outstanding**: it converts the
      large-field overround from an untestable hypothesis into a testable one.
- [ ] **Merge sweep above 10 ms** (0.01, 0.03, 0.1, 0.3, 1.0 s) — the last open
      diffusion question. Tests whether `n` keeps climbing as the sub-second
      tail is merged away and whether the diagnostic rejections fall with it.
- [ ] **Wire the stationarity p-value into `fit_diffusion.py`** — reported
      beside every `n`, as KS and Ljung-Box already are. A flagged window's
      branching ratio prints as uninterpretable rather than being averaged in.
- [ ] **Recompute the bimodality on unflagged windows only** — does the
      0.23 / 0.88 split survive once drift-contaminated windows are excluded?
- [ ] **Backtest engine** — chronological replay, no lookahead, fills capped at
      observed depth. Turns "detected" into "would have made money".
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
- [x] **Large fields were never collected** — 0 of 2,522 three-leg baskets
      complete, nothing with 4+ legs polled. The overround hypothesis is
      untested and the current collection design cannot test it.
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

- [ ] **Event-complete universe selection** — see "Do next"
- [ ] **Re-run the bucket-sum scan** once complete families exist
- [ ] **Backtest engine** — chronological replay, realistic fills
- [ ] **Monotonicity detector** — nested threshold families
- [ ] **Fair-value model** — ONE domain, finished
- [ ] **Calibration** — Brier with decomposition, log loss, reliability, skill
- [ ] **Fractional Kelly sizing** — capped by observed depth

## Project 2 — open

- [ ] **Merge sweep above 10 ms** — see "Do next"
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
