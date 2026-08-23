# Quant research

Two research projects sharing one data and statistics layer.

**Kalshi mispricing engine** — detects structural (model-free) and fair-value
(model-dependent) mispricings in Kalshi event contracts, sizes them against
order book depth and the exchange's fee schedule, and evaluates them in an
event-driven backtest.

**Information diffusion model** — fits a Hawkes process to the arrival of
social and news events around scheduled announcements, validates the fit by
time rescaling, and relates the fitted diffusion speed to abnormal returns.

The two share `quant.common` (schemas, API clients, statistics, plotting) and
have no dependency on each other.

## Status

Early. Collection and the fee model are in; strategies and the diffusion model
are not. See `docs/` for the methodology write-ups each project is building
toward — those are the deliverable, not the code.

| Component | State |
| --- | --- |
| `common/db/schema.py` | written, 34 tests |
| `common/api/kalshi.py` | written, 15 tests, unverified against live API |
| `ingest/kalshi_collector.py` | written, 17 tests |
| `kalshi/fees.py` | written, 32 tests, unreconciled against a real fill |
| `common/db` connection, queries | not started |
| `common/api` reddit, market data | not started |
| `common/statistics` | not started |
| `kalshi/strategies`, `backtest` | not started |
| `diffusion/` | not started |

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # then fill in credentials
pytest
```

Tested on Python 3.11. The whole test suite runs offline: request signing is
checked against a throwaway key and parsing against recorded response shapes,
so no test needs credentials or the network.

## Collecting

Order books cannot be backfilled. The dataset starts when the collector first
runs, which makes getting it running the highest-priority task in the repo and
is why it exists before anything that consumes its output.

```bash
python scripts/collect_kalshi.py --discover                    # what would be collected
python scripts/collect_kalshi.py --auto --interval 30 --out ./data
```

`--auto` selects open events flagged `mutually_exclusive` with two or more
markets, which is exactly the family the bucket-sum check applies to.

**This has to run somewhere that stays up.** A laptop that sleeps loses the
hours it was asleep, permanently. A `systemd` unit, a `launchd` agent, or the
cheapest available VPS all work; the process is restart-safe and appends to the
current day's file rather than truncating it.

Raw responses are written verbatim to `data/raw/*.jsonl` before any parsing,
one JSON object per line, flushed and fsynced periodically. A parsing bug
therefore costs a reparse rather than a day of data. `parse_raw_file` turns
those into `OrderBookSnapshot` objects offline, skipping records it cannot
read rather than aborting the file.

## Layout

```
src/quant/common/      schemas, API clients, statistics, plotting
src/quant/kalshi/      strategies, backtest, sizing, execution
src/quant/diffusion/   hawkes, event_study, pipelines
tests/unit/            no network or stored data required
tests/integration/     marked `integration`, needs credentials
docs/                  methodology write-ups
scripts/               CLI entry points
```

## Notes on the design

A few decisions that are load-bearing and worth stating up front, because they
constrain everything downstream.

**Order books are stored in YES terms only.** A resting NO bid at `q` is the
same as a YES ask at `100 - q`, so keeping both sides independently invites
them to disagree. NO-side views are derived. This assumption should be
re-verified against live API payloads before the ingestion layer is trusted.

**Full book depth, not top-of-book.** Sizing an arbitrage requires knowing how
many contracts can be lifted before the edge is gone. A schema that only keeps
the best bid and ask cannot answer the capacity question, which is the question
that decides whether a strategy is interesting.

**Derived quantities are properties, not fields.** Branching ratio, spread,
unrealised P&L and portfolio equity are computed from the fields they depend
on, so they cannot be stored inconsistently.

**Timestamps are timezone-aware UTC, enforced at construction.** Naive
datetimes are rejected rather than coerced.

**No `tick` dependency.** The usual off-the-shelf Hawkes library is effectively
unmaintained and does not build on current Python. The MLE is written directly
against `scipy.optimize`, which is also the better thing to be able to explain.

**Sharpe is not stored on a portfolio snapshot.** It is a property of a return
series, not of an instant; storing it per-snapshot invites quoting a number
computed over an accidental window.

**Fees are computed in exact decimal arithmetic.** In binary float,
`20/100 * 80/100` is `0.16000000000000003`, so a fee that is exactly 11200
cents ceilings to 11201 and the formula stops being symmetric about 50¢. That
is a one-cent error in the function that decides whether an edge survives its
costs, so it is not an acceptable rounding artefact.

## Open assumptions

Things asserted here that have not yet been checked against reality, listed so
they do not quietly become load-bearing:

- The YES/NO book complementarity, against live API payloads.
- The fee schedule's rounding granularity and its per-series multiplier table
  (some series carry multipliers from 0 to 2), against a settled trade on the
  account.
- Kalshi's applicable rate limit. The client's default of 8 req/s is a guess
  chosen to be conservative, not a published figure.

## Disclaimer

Research and portfolio work. Not investment advice.
