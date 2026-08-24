# Tasks

Goal: two defensible portfolio projects for quant researcher roles, no PhD.
Scope doc written 2026-08-21. Today is 2026-08-23 — day 3 of a 6-week plan.

## Status

**Kalshi: ahead of schedule.** The scope doc allowed weeks 1–2 for the ingestion
pipeline and storage. That is done, plus the fee model, selection layer and a
running collector. Data has been accruing since 05:17 UTC today.

**Diffusion: not started, and it has the same clock.** Reddit history you do not
collect today cannot be reconstructed later. This is the one thing genuinely at
risk.

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

## Active

- [ ] **Start the Reddit collector** — Project 2, unblocked, same no-backfill clock
- [ ] **Move collection somewhere that stays up** — it died once already, after
      90 minutes, for reasons still unknown. A cheap VPS with systemd removes
      this whole class of problem.
- [ ] **Commit the working tree locally** — files are current, git history is not

## Blocked

- [ ] **Reconcile the fee model against a settled fill** — needs a real trade.
      Rounding granularity and per-series multipliers (0 to 2) still unverified.
- [ ] **Verify YES/NO book complementarity against live payloads** — the
      single-book schema rests on this

## Project 1 — Kalshi

- [ ] **Bucket-sum detector** — short direction first; it is the identifiable one
- [ ] **Monotonicity detector** — nested threshold families
- [ ] **Backtest engine** — chronological replay, no lookahead, realistic fills
- [ ] **Fractional Kelly sizing** — capped by observed book depth
- [ ] **Fair-value model** — pick ONE domain, Fed rates or weather
- [ ] **Calibration** — Brier with decomposition, log loss, reliability, skill score

## Project 2 — Diffusion (nothing started)

- [ ] **Reddit ingestion** — ticker extraction needs a hand-labelled false-positive check
- [ ] **Earnings / Fed / macro calendar** — scheduled events are the clean identification
- [ ] **Minute-level market data** — Polygon or Alpaca
- [ ] **Hawkes MLE** — recover known parameters from simulation FIRST
- [ ] **Time-rescaling diagnostics** — KS test on rescaled residuals
- [ ] **Event study** — abnormal returns, named benchmark, clustered standard errors

## Write-ups — the actual deliverable

- [ ] **docs/kalshi_methodology.md** — skeleton written; the fee-ceiling result
      and the direction asymmetry are ready to write up now
- [ ] **docs/diffusion_methodology.md** — skeleton written, needs everything
- [ ] **docs/results_summary.md** — one page

## Watch out for

- **Project 2 drift.** Still the largest risk. The scope doc calls it the
  stronger signal for a *research* role, and it is at zero on day 3.
- **Scope creep on fair value.** One domain, finished, beats three started.
- **A finding that says "no edge" is still a finding.** The fee ceiling is
  exactly that, and it is worth more than a curve-fit backtest.

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
      (`yes_ask_dollars`, `volume_fp`); the documented names do not exist
- [x] ~~Both trade directions measured~~ (08-23) — with the identifiability asymmetry
- [x] ~~Leg cap and fee-aware ranking~~ (08-23) — 3 families became 71
- [x] ~~Heartbeat logging and metadata dedup~~ (08-23)
- [x] ~~Crash resilience and file logging~~ (08-23) — after a silent death
- [x] ~~Double-clickable launchers~~ (08-23) — no more relative-path breakage
- [x] ~~**Collection running**~~ (08-23) — the dataset exists and is growing
