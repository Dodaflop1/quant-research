# Start here

**Read [`HANDOFF.md`](./HANDOFF.md) before doing anything else.** It is written
for a reader with zero context and contains the project's goals, current state,
every established finding with its numbers, and — importantly — a list of traps
already hit, so you do not rediscover them.

Then, depending on what you are asked to do:

For **U.S. equities application/product development**, read
[`docs/equities_product_plan.md`](./docs/equities_product_plan.md). It records
the current product direction, known correctness gaps, ordered milestones and
continuation protocol. The legacy research priorities below remain separate.

| Task | Read |
|---|---|
| The next piece of work | [`docs/power_law_spec.md`](./docs/power_law_spec.md) |
| Why a result is what it is | [`docs/findings/README.md`](./docs/findings/README.md) |
| Project 1 write-up | [`docs/kalshi_methodology.md`](./docs/kalshi_methodology.md) |
| Project 2 write-up | [`docs/diffusion_methodology.md`](./docs/diffusion_methodology.md) |
| What is outstanding | [`TASKS.md`](./TASKS.md) |
| Server / collection | [`deploy/README.md`](./deploy/README.md) |

## Conventions this repo is held to

- **Every number reported must be traceable to code or data.** Nothing asserted.
- **Simulate before fitting.** Every estimator is validated against data whose
  answer is known before it touches real data. This has already caught two bugs
  that a single well-behaved fit hid completely.
- **Sections that are not done say so** rather than being omitted. A reader must
  be able to tell "no edge here" from "not looked at yet".
- **A rejection is decisive; a pass is weak evidence.** Report the number of
  residuals with every p-value.
- **Never quote a statistic you have not defined.** A summary line once reported
  a "shrinkage" of +0.145 that was a difference of medians from different
  markets; the real figure was +0.004.
- **Boundary solutions are non-convergence.** Do not quote their standard errors.
- Comments explain *why*, especially where a choice looks arbitrary.

## Do not

- Commit `.env`, `certs/`, or `data/` — all gitignored, and verified absent.
- Handle the Kalshi private key as text. It can place real trades. Move it as a
  file only.
- Concatenate the two order book panels. The 08-23 → 08-24 laptop data used a
  different universe from the server data.
- Fit raw trade prints. Aggregate them into order arrivals first — a third to
  two thirds of prints are simultaneous fills from single orders, and fitting
  them directly is degenerate *and reports convergence*.

## Verify your environment works

```bash
pytest tests/unit          # 254 tests
python scripts/hawkes_recovery.py --reps 20   # 4 validation studies
```
