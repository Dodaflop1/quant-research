# Tasks

Goal: two defensible portfolio projects for quant researcher roles, no PhD.
Scope doc written 2026-08-21. Today is 2026-08-24 — day 4 of a 6-week plan.

## Status

**Kalshi: ahead of schedule.** Ingestion, storage, fee model, selection layer
and a running collector are done. Data accruing since 08-23 05:17 UTC.

**Diffusion: unblocked and started.** The Reddit dependency is gone. Kalshi
trade prints are *backfillable* — `/markets/trades` serves ~3 months and
`/historical/trades` serves older, both with `min_ts`/`max_ts` and cursor
pagination. Project 2 now runs on order flow, with Reddit as an additive second
point process if approval ever lands.

That single API fact removed the biggest schedule risk in the plan. There is no
longer a no-backfill clock on Project 2.

## Live now

- Collector running: 150 markets across 71 families, 60s interval, ~35s cycles
- 90,000+ order book snapshots, zero errors, clean 60s sampling with no gaps
- Logging to `data/collector.log`; double-click `tail_log.cmd` to watch

## Findings so far

These are research results, not progress notes. They belong in the write-up.

- [x] **The long direction is closed at the touch.** Every family sampled had an
      ask sum above 100c (100.4–109.2c). Summing asks means crossing the spread
      on every leg. Whether it ever opens transiently is what the time series is
      for.
- [x] **Fees, not prices, bound the strategy.** The taker fee rounds UP to a
      whole cent per leg against a fixed 100c payout, so an N-leg basket owes at
      least N cents. Past ~10 legs no dislocation can close the gap. A 184-leg
      field owes more than the basket can ever pay.
- [x] **The two directions need different things.** Buying needs collective
      exhaustiveness, which the exchange does not certify and prices cannot
      settle. Selling needs only mutual exclusivity, which it does flag — so the
      short direction is the identifiable one.
- [x] **Large-field overround is structural.** The minimum tick props up every
      longshot, so a big field's ask sum runs far above 100c by construction.
- [x] **Capacity is not volume.** One family showed 368,438 volume and a single
      contract resting at the ask.
- [x] **The panel was unbalanced and nobody would have noticed.** A coverage
      audit of the first 39.3 hours found 312,386 snapshots across 298 tickers
      and **zero** that spanned the whole window. Discovery re-selects by live
      volume on every start, so each of three restarts swapped part of the
      panel: 150 entered late, 99 stopped early, 49 covered only a middle
      slice. The log said "0 errors" throughout, and it was telling the truth —
      it just answers a different question. Fixed by pinning the universe.
- [x] **Sampling itself is clean.** Per market: median 60s, p95 60s, p99 60s,
      max 66s. Two outages totalling 15.8 minutes, uptime 99.33%, and every
      per-market gap explained by a collector outage rather than a ticker
      quietly failing. This is the number the methodology section can defend.
- [x] **Seasonality alone fabricates a branching ratio of 0.79.** On simulated
      data with *zero* self-excitation — an inhomogeneous Poisson process with a
      sinusoidal rate — a constant-baseline Hawkes fit reports n = 0.79, and the
      residual diagnostics catch it only 12% of the time. Published order-flow
      branching ratios sit near 0.8–0.9. This is the Hardiman–Bouchaud critique
      of Filimonov–Sornette, reproduced on our own estimator, and it constrains
      how the real fit has to be done. See `results/diffusion/`.

## Active

- [ ] **Restart the collector to pick up the pinned universe** — `run_collector.cmd`
      now passes `--tickers-file .\data\universe.txt`. Until it restarts, the
      panel is still whatever the last start chose. One restart costs ~1 minute
      of data and makes every series from here continuous
- [ ] **Run the trades backfill** — `python scripts/backfill_trades.py --days 90`
      then `--report`. Resumable; interrupt it freely
- [ ] **Move collection somewhere that stays up** — it died once already, after
      90 minutes, for reasons still unknown. A cheap VPS with systemd removes
      this whole class of problem
- [ ] **Deseasonalised Hawkes fit** — the estimator is validated but a naive fit
      on real trade times is now known to be wrong. Needs either a time-varying
      baseline or short local windows, reported both ways

## Blocked

- [ ] **Reconcile the fee model against a settled fill** — needs a real trade.
      Rounding granularity and per-series multipliers (0 to 2) still unverified
- [ ] **Verify YES/NO book complementarity against live payloads** — the
      single-book schema rests on this
- [ ] **Reddit API approval** — application submitted 08-23, no ETA. No longer
      blocking anything; it is now upside, not a dependency

## Project 1 — Kalshi

- [ ] **Bucket-sum detector** — short direction first; it is the identifiable one
- [ ] **Monotonicity detector** — nested threshold families
- [ ] **Backtest engine** — chronological replay, no lookahead, realistic fills
- [ ] **Fractional Kelly sizing** — capped by observed book depth
- [ ] **Fair-value model** — pick ONE domain, Fed rates or weather
- [ ] **Calibration** — Brier with decomposition, log loss, reliability, skill score

## Project 2 — Diffusion

- [x] ~~Hawkes simulation~~ (08-24) — cluster construction with parentage, plus
      Ogata thinning for the supercritical case
- [x] ~~Hawkes MLE~~ (08-24) — O(n) recursion, multi-start, bounded, asymptotic
      standard errors from a numerical Hessian
- [x] ~~Parameter recovery study~~ (08-24) — bias under 4%, CI coverage 85–95%
- [x] ~~Negative control~~ (08-24) — Poisson data gives median n = 0.033, 95th
      percentile 0.19. That percentile is the noise floor any real estimate
      must clear
- [x] ~~Time-rescaling diagnostics~~ (08-24) — KS *and* Ljung-Box, because KS
      alone cannot see autocorrelation
- [x] ~~Trades backfill pipeline~~ (08-24) — day-chunked, resumable, both
      endpoints, deduplicated on trade_id
- [ ] **Bivariate Hawkes** — buy-initiated and sell-initiated flow as two
      processes, with cross-excitation. The taker side is already parsed
- [ ] **Event alignment** — Fed / CPI / jobs release times. Scheduled events are
      the clean identification: the announcement time is exogenous
- [ ] **Price discovery link** — does branching ratio predict the speed at which
      the market converges to its settlement value?
- [ ] **Reddit as a second process** — if approval lands. Social → trade
      cross-excitation is strictly more interesting than either alone

## Write-ups — the actual deliverable

- [ ] **docs/kalshi_methodology.md** — the fee-ceiling result and the direction
      asymmetry are ready to write up now
- [ ] **docs/diffusion_methodology.md** — the validation studies are ready to
      write up now, before any real-data fit exists
- [ ] **docs/results_summary.md** — one page

## Watch out for

- **Quoting a branching ratio without deseasonalising.** We now have our own
  simulation showing it produces 0.79 out of nothing. Doing it anyway would be
  the single most attackable thing in either project.
- **Scope creep on fair value.** One domain, finished, beats three started.
- **A finding that says "no edge" is still a finding.** The fee ceiling is
  exactly that, and it is worth more than a curve-fit backtest.
- **Project 2 drift** — downgraded. It has code, tests and results as of day 4.
- **Trusting the log over the files.** "0 errors" was true and useless. Every
  claim about the data — sampling rate, coverage, panel composition — gets
  checked against the raw files before it goes in a write-up.

## Done

- [x] ~~Architecture and Pydantic schema layer~~ (08-23) — 34 tests
- [x] ~~Corrected Hawkes branching ratio~~ (08-23) — was `α*β/λ₀`, is `α/β`
- [x] ~~Fixed unbuildable requirements.txt~~ (08-23) — 3 bad packages
- [x] ~~Kalshi REST client~~ (08-23) — RSA-PSS signing, verified live
- [x] ~~Order book collector~~ (08-23) — raw-JSONL-first durability
- [x] ~~Kalshi fee model~~ (08-23) — exact decimal arithmetic
- [x] ~~Production API key installed~~ (08-23) — demo and prod keys separated
- [x] ~~Selection layer~~ (08-23) — 65k markets down to a workable universe
- [x] ~~Fixed the payload parser~~ (08-23) — live fields are unit-suffixed
- [x] ~~Both trade directions measured~~ (08-23) — with the identifiability asymmetry
- [x] ~~Leg cap and fee-aware ranking~~ (08-23) — 3 families became 71
- [x] ~~Heartbeat logging and metadata dedup~~ (08-23)
- [x] ~~Crash resilience and file logging~~ (08-23) — after a silent death
- [x] ~~Double-clickable launchers~~ (08-23) — no more relative-path breakage
- [x] ~~**Collection running**~~ (08-23) — the dataset exists and is growing
- [x] ~~Committed to git and published to GitHub~~ (08-23) — certs, .env and
      data all confirmed ignored
- [x] ~~Reddit API application submitted~~ (08-23) — awaiting approval
- [x] ~~**Project 2 unblocked**~~ (08-24) — trades are backfillable; the
      six-week collection clock does not apply
- [x] ~~Coverage audit against the raw files~~ (08-24) — outages, true
      per-market sampling interval, panel balance
- [x] ~~Universe pinning~~ (08-24) — `--tickers-file` and `--save-universe`;
      current 150-ticker panel captured to `data/universe.txt`
- [x] ~~Fixed two bugs in the audit tool itself~~ (08-24) — it reported
      "median 1s" for one-minute data by measuring across markets instead of
      per market, and matched gaps to outages by exact second
- [x] ~~Caught an optimiser runaway with the validation study~~ (08-24) —
      unbounded log-parameters let L-BFGS-B reach alpha ≈ 1e190 and report
      success. Invisible at 20,000 events, fatal at 2,000
