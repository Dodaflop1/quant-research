# Project handoff — read this first

*Rewritten 2026-08-25, end of day 5 of 42. Self-contained: assumes you know
nothing about this project.*

Two portfolio projects for quant researcher roles, no PhD. Working copy at
`C:\Users\lucae\Downloads\quant-research`; the GitHub repo is the durable one.

**26 shipped · 22 findings held · 16 open · 2 blocked · 283 tests passing.**

---

## Read this paragraph first

Yesterday this project believed it had found reflexivity in event-contract trade
arrivals: branching ratios around 0.88 with long excitation half-lives. **Today
that claim is substantially dismantled, by this project's own controls.** A 2x
rate step applied to data with *zero* self-excitation produces a fitted
branching ratio of 0.799 — and the time-rescaling diagnostics accept it 24 times
out of 24. On the real panel, 12 of 18 windows drift more than their own fitted
model allows, and the branching ratio tracks that drift at Spearman +0.725
(p = 0.001).

**This is the most valuable material in the repo.** Do not try to rescue the
original claim. The write-up is "I found a result, doubted it, built the control
that would kill it, and it did" — a stronger interview story than a branching
ratio would ever have been.

## Where to start, in order

1. **Event-complete universe selection** in `src/quant/ingest/discovery.py`.
   The collector ranks individual tickers by volume; a 20-leg field spreads its
   volume across 20 tickers so few clear the threshold, and the large-field
   overround (243c across 293 families) has **never been tested** — 0 of 2,522
   three-leg baskets were ever complete and nothing with 4+ legs was polled.
   Select whole mutually-exclusive families against a leg budget, recollect,
   then re-run `scripts/detect_bucket_sum.py`. Highest-value item open.
2. **Merge sweep above 10 ms** — set `MERGE_WINDOWS` in
   `scripts/sensitivity_sweep.py` to `[0.01, 0.03, 0.1, 0.3, 1.0]` and rerun.
   Tests the microstructure reading below. The last open diffusion question.
3. **Backtest engine** — Project 1 has a detector but no replay harness. This is
   the gap that makes the portfolio look half-finished.

`TASKS.md` has the full board.

## Infrastructure — finished, do not re-litigate

- Oracle Always Free, `159.54.168.0`, **VM.Standard.E2.1.Micro** (ARM A1 was out
  of capacity; 954 MB RAM + a 2 GB swapfile).
- App at `/opt/quant-research`, service account `collector`, venv with only
  `deploy/requirements-collector.txt` (4 packages).
- `kalshi-collector.service` (`Restart=always`) **plus** `kalshi-watchdog.timer`,
  a 5-minute data-freshness check. `Restart=always` cannot see a process that
  runs but collects nothing; the watchdog can.
- SSH: `ssh -i $env:USERPROFILE\.ssh\oracle_key ubuntu@159.54.168.0`
- Run repo commands as the owner: `sudo -u collector python3 ...`
- Audit CLEAN: 150/150 markets full-span, zero outages, median and p95 60 s.

## Data on hand

| | |
|---|---|
| Server order books | from 2026-08-25 00:53 UTC, 150 pinned long-lived markets |
| Laptop order books | 08-23 to 08-24, **different universe — never concatenate** |
| Slow-arm trades | 151,477 over 89 days; 9 markets clear 2,000 *orders* after merging |
| Fast-arm trades | 3.56M in 9h13m, exchange-wide, 1.08 GB (gitignored) |

## Findings — Project 2 (diffusion)

1. **A 2x rate step fabricates `n` = 0.799** on zero-excitation data; 0.034 with
   no step; 4x gives 0.942. (`scripts/nonstationarity_study.py`, Study E.)
2. **The diagnostics rejected 0 of 24** on that fabricated fit. Time-rescaling
   tests whether the fitted *intensity path* explains the arrivals, and a slow
   kernel tracking a rate step does. **It cannot distinguish self-excitation
   from a time-varying baseline.** A diagnostic pass is not validation.
3. **The real panel drifts.** 12/18 windows exceed their own fitted model
   (parametric bootstrap, 200 sims); Spearman(rate ratio, `n`) = +0.725,
   p = 0.001. The 5.62x and 4.43x windows carry `n` = 0.937 and 0.904 — on the
   curve traced by simulated data with no excitation at all.
4. **Stationarity and diagnostics are independent** (Fisher p = 0.344), as
   predicted. So the 61-72% diagnostic rejection rate **remains unexplained**.
5. **Power-law misspecification ruled out** (Study C): no bimodality at any eps,
   the n/half-life relation has the wrong sign, rejection 10% against 72%.
6. **Power-law noise floor 0.141** (the exponential kernel's is 0.19).
7. **eps is not identified** at ~2,400 events — se(n) ~ 7%, se(eps) ~ 22-54%,
   identified in 0/12 fits. `PowerLawParameters.eps_is_identified` enforces it.
8. **Seasonality is exonerated here** — median per-market shrink +0.000 /
   -0.0002 / +0.004 at periods of 900 / 3600 / 86400 s.
9. **~30% of "trades" are exact ties.** One aggressive order emits several
   prints at one instant. Unmerged, every window pinned the beta bound.
10. **Merge tolerance is load-bearing.** At 0.1 s, naive `n` jumps 0.466 -> 0.723
    on 0.4% more prints merged.
11. **Some fits capture microstructure, not diffusion.** `CONTROLH-2026-R w0`
    fits a 45 ms half-life on a window spanning weeks — and it is the true MLE
    by 130+ log-likelihood units. The model is inadequate, not the optimiser.
    Item 2 above tests this.

## Findings — Project 1 (Kalshi)

1. **YES/NO complementarity verified.** 108,489 two-sided books, **zero**
   crossings, median bid sum 99.0c. `parse_orderbook`'s `ask = 100 - no_bid` is
   sound, and every price in Project 1 rests on it.
2. **Fees killed 5,792 of 5,792** baskets whose bid sum exceeded 100c. Closest
   miss 0.1c. The market sits *on* the fee-adjusted no-arbitrage bound.
3. **Large fields were never collected** — see "where to start", item 1.
4. **Fee ceiling** — an N-leg basket owes at least N cents. Arithmetic.
5. **Direction asymmetry** — short needs only mutual exclusivity (flagged by the
   exchange); long needs exhaustiveness (not certified).
6. **Long direction closed at the touch** — ask sums 100.4-109.2c.
7. **Large-field overround structural** — 243c across 293 families.
8. **Capacity is not volume** — 368,438 in volume, one contract at the ask.
9. **36.6% of snapshots have no ask, 30% no book at all.** Effective panel ~86
   of 150 — a stated limitation, and the reason for the universe fix.
10. **The 98-99c markets are untradeable** — no offer exists, and the only
    executable trade carries a fee at or above its whole expected value.

## Where the evidence lives

```
docs/findings/what_the_slow_arm_is.md       Study E + the sensitivity sweep
docs/findings/window_stationarity.md        real-panel stationarity + a retraction
docs/findings/power_law_studies.md          Studies B, C, D
docs/findings/orderbook_complementarity.md  the verified price basis
docs/findings/bucket_sum_scan.md            the fee-bound result
docs/power_law_review.md                    review + resolution
docs/kalshi_methodology.md                  331 lines
docs/diffusion_methodology.md               347 lines
results/...                                 cited JSON is un-gitignored deliberately
```

## Non-negotiables

- **`certs/kalshi_prod.pem` can place real trades.** Never paste its contents
  anywhere — not a terminal, a chat window, an editor, or a file. Move it with
  `scp` only. Only the prod key goes on the server; `kalshi_demo.pem` stays
  local. Verify `.env` with `grep KALSHI_PRIVATE_KEY_PATH`, never `cat` — it
  holds the API key ID.
- **`.env`, `certs/`, `data/`, `data_*/`, `*.jsonl` are never committed.** A
  1.08 GB file reached a commit once; git history is forever.
- **`results/` is gitignored except files a findings doc cites by path.** Keep
  it that way: a cited number whose source is absent cannot be checked.
- **Avoid `git status` / `git diff` through the Claude device bridge.** They
  take `index.lock` and have stranded the repo twice. Use `git log`,
  `git rev-parse`, `git ls-files`, `git for-each-ref`.
- **`device_bash` cannot delete files.** `mv` to a `_to_delete/` folder instead.

## Delegating to a second model

Luca has unlimited access to a local Qwen3.5-122B. Two rounds of evidence:

- **It works well from a skeleton.** Given `power_law_studies.py` with driver,
  seeding, result types and reporting finished and three `NotImplementedError`
  bodies carrying contracts in their docstrings, it produced real, runnable
  implementations.
- **It fails from prose.** Asked to "fix" code against a written review it
  returned the file byte-identical but for one added comment. Asked to write a
  script against a *described* CLI it had not seen, it hallucinated the ticker
  list, invented `--holdout 500` for a flag that takes a fraction (default
  0.25), and ran every fit twice.
- **Rule: give it the file, not a description of the file.** It cannot
  introspect the repo. Anything touching an API, a schema, or a flag's units
  needs the source pasted in.
- **Check its statistics, not just its code.** It answered "is this bimodal?"
  with a variance, and computed a correlation pooled across a swept parameter
  where the within-group sign was the opposite (+0.48 against -0.80).

## Reproducing today's work

```bash
pytest tests/unit -q                                   # 283 passed
python scripts/nonstationarity_study.py --reps 24
python scripts/window_stationarity.py --data ./data --sims 200
python scripts/sensitivity_sweep.py --data ./data      # ~1h, caches per cell
python scripts/verify_complementarity.py data/raw/kalshi_orderbook_2026-08-24.jsonl
python scripts/detect_bucket_sum.py \
    --books data/raw/kalshi_orderbook_2026-08-24.jsonl \
    --metadata data/raw/kalshi_metadata_2026-08-24.jsonl
```

Everything is seeded from `BASE_SEED = 20260825`; a rerun is bit-identical.
Order book files (~175 MB) are too large for the Claude device bridge and must
be processed on the machine holding them. Trade files (~46 MB) stage fine.

## Watch out for

- **Quoting a branching ratio as reflexivity.** Attach the stationarity flag.
- **Treating a diagnostic pass as validation.** Demonstrated false.
- **Reporting eps.** Identified in 0/12 fits. Don't route around the type.
- **Concatenating the two order book panels.** Different universes.
- **Presenting the two-leg bucket-sum result as new.** It restates the
  complementarity finding — same quantity, same 99.0c median.
- **Scope creep on fair value.** One domain finished beats three started.
- **Trusting the log over the files.** "0 errors" was true and useless.
