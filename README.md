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

Early. The shared schema layer and its tests are written; ingestion, strategies
and models are not. See `docs/` for the methodology write-ups each project is
building toward — those are the deliverable, not the code.

| Component | State |
| --- | --- |
| `common/db/schema.py` | written, 34 tests |
| `common/db` connection, queries | not started |
| `common/api` clients | not started |
| `common/statistics` | not started |
| `kalshi/` | not started |
| `diffusion/` | not started |

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # then fill in credentials
pytest
```

Tested on Python 3.11.

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

## Disclaimer

Research and portfolio work. Not investment advice.
