# Equities research product — development plan and working memory

Updated: 2026-09-16. Status: Milestone 1 ledger implemented locally; remaining milestones are pending.

## Product direction

Build a useful research application for people who can express market questions but need help turning them into explicit, reproducible experiments. Prioritize correctness, understandable results, and repeat use. Coffee chats, pitches, and fundraising demos are no longer the development objective.

Core workflow: choose data → define a question → inspect and confirm assumptions → run → understand evidence → save → compare or reproduce.

Initial user hypothesis: independent researchers and students working with daily U.S. equities. Validate this through actual use; it is not established customer demand. Success means users can finish an experiment, explain its assumptions and limitations, reproduce it, and return with another question. Positive strategy returns are not a product success criterion.

## Current state and known gaps

Existing prototype: Streamlit UI, typed hypothesis, CSV/Yahoo data, reversal/momentum and volume filters, optional AI interpretation, daily portfolio chart, trade records, metrics, t-test, sensitivity grid, historical split. Implementation lives in app/app.py and src/quant/equities/. Existing Kalshi/diffusion work has its own plans and must be preserved.

Known gaps after Milestone 1:

- Downloading begins at the experiment start without indicator warm-up; provider end-date semantics differ from inclusive UI dates. CSV and downloaded-frame validation also differ; missing tickers can silently disappear.
- Raw JSON dominates results. The benchmark schema field does not produce a benchmark comparison. Saved, immutable experiment records are absent.

Earlier chat statements about approximately 20% return and statistical significance are provisional prototype outputs. Recalculate after engine fixes; do not preserve them as validated product claims.

## Milestone 1 — trustworthy calculations (next implementation task)

Scope: one consistent long-only, daily-close execution model. Hide shorting until borrowing, accounting, and costs have explicit support.

Proposed baseline contract: signals observed at close t; entry at next available trading close; exit after H subsequent trading intervals. Each new position receives at most initial capital / maximum positions, subject to available cash including costs. No leverage, no simultaneous duplicate ticker positions, unused cash earns zero. Hold shares fixed until exit; rank simultaneous candidates deterministically. This is a starting contract to implement and document, not current behavior.

Build one ledger containing signal/entry/exit dates, prices, shares, fees, cash and daily holdings. Derive trade records, equity, metrics and sensitivity from it. Keep per-event research returns separately labeled; overlapping event returns must not be presented as independently invested portfolio returns.

Use one data validator: finite positive prices, nonnegative volume, valid dates, explicit duplicate handling, missing ticker/coverage report, adjustment/source metadata. Obtain sufficient preceding history for features. Choose completed-trade-only period comparisons initially: exclude entries whose full holding is unavailable within the period and report exclusions. Preserve earlier data for feature warm-up only.

Completed 2026-09-16: `build_ledger` now drives both trade records and daily portfolio output. It enters on the next ticker close, exits after the requested number of trading intervals, charges entry and exit costs, holds fixed shares, caps each position at initial capital / top_n, keeps cash nonnegative, excludes holdings that cannot close within the requested period, and measures drawdown from starting capital. The sensitivity grid now reports return, drawdown, completed trades and exclusions rather than an invalid event-return Sharpe. The UI uses “earlier/later comparison” rather than calling a historical period untouched. Focused tests cover timing, costs, ledger reconciliation, boundary exclusions and drawdown; the built-in sample ends at $10,080.77 from $10,000 under the new contract.

Remaining Milestone 1 work: validate missing price coverage while a holding is open, make feature warm-up and provider date conventions explicit, and add benchmark support as part of the results workbench.

## Milestone 2 — a clear experiment workbench

Four basic areas: Data, Experiment, Results, Saved research. Keep Streamlit and the existing deterministic Python modules initially.

Data: show active source, dates, tickers, missing coverage and adjustment assumptions; cache/persist snapshots so ordinary UI changes do not download fresh data. Provide download/upload in the same normalized format.

Experiment: ship three constrained templates: high-volume selloff/rebound; SPY after three consecutive down sessions; large daily gains followed by momentum or reversal. Consecutive declines require a dedicated condition, not a cumulative three-day return substitute. Manual editing stays available. AI may propose supported fields and ask for clarification; it must reject unsupported requests instead of silently approximating them.

Results: plain-language summary, event/position counts, exposure, net return, maximum drawdown, strategy and SPY growth charts, underlying records and assumptions. Define “outperform”: same-date event excess return and whole-period portfolio benchmark comparison answer different questions. Align benchmark dates and price conventions; disclose different exposure and costs. Move raw JSON/advanced statistics into details.

Acceptance: a user can define, confirm and finish a supported experiment; empty results explain which filters removed events; dates and percent/dollar units are clear; unsupported input produces useful feedback; main workflow passes a Streamlit UI smoke test.

## Milestone 3 — saved, reproducible research

Use local SQLite for experiment metadata plus immutable data files initially; no accounts required. Record unique run ID, hypothesis/schema, source and dataset hash, code/engine version, costs, dates, outcome, exclusions, and parent run when edited. Save successful and unsuccessful runs. Export a portable report and experiment bundle. Define migration/version handling before extending the schema.

Acceptance: reopening a run reproduces its recorded result from its snapshot without network access; changing a rule creates a new run rather than overwriting history; users can compare two runs and see exactly what changed.

## Milestone 4 — honest research validation

Replace “untouched” with “historical comparison” by default. A reserved test needs a rule version committed before reveal and an exposure/reveal record; software cannot prove a person has never viewed that market history elsewhere. Once inspected, label it evaluated and keep tuning history visible. Keep sensitivity exploratory and do not choose the best setting automatically.

Add dependence-aware uncertainty (for example, a documented block bootstrap with fixed seed), sample limitations, and the number of variants tried. Avoid a binary “profitable/significant” badge. Expand universes only with clear selection provenance; today's surviving stocks are not a historical unbiased universe.

Acceptance: period comparisons have no outcome leakage; changing a rule after reveal is visible; repeat uncertainty calculations are deterministic and validated on simulated data; no annualization uses the wrong observation frequency.

## Milestone 5 — repeat use, then shared access

Observe five prospective users completing their own questions. Track completion, confusing steps, reproducibility and returning use; collect evidence before fixing numerical growth targets. Prioritize recurring friction. When local workflow is reliable and sharing demand exists, scope hosted access, authentication, data permissions and a suitable deployment separately. Shared experiment links precede social feeds.

## Add, subtract, refine

Add: reconciled ledger, benchmark, coverage diagnostics, three supported templates, run history, export, reproducible snapshots.

Subtract from default UI: raw JSON, misleading sensitivity Sharpe, untracked “untouched” claims, unsupported shorting, any implication that the engine automatically finds optimal holds.

Refine: entry/exit timing, capital allocation, costs, overlap, dates, statistical explanations, AI confirmation and no-result messages.

Defer: marketplace, payments, copy trading, brokerage orders, social feed, strategy rankings, intraday/options/crypto, unrestricted code generation, frontend rewrite, additional data vendors without a concrete need. Do not delete existing research projects.

## Continuation protocol

For future equities product work, read this file and the repository instructions first. Start with the first unfinished milestone; avoid restarting the strategy discussion. This plan authorizes sequencing, not automatic implementation of all milestones during a planning request.

Update this document at the end of each implementation task with completed items, checks, unresolved issues, decision changes, and the next concrete task. Distinguish implemented behavior from proposals. Store customer learning alongside the relevant milestone. Repository files are the durable memory; do not promise recall without access to them.

Next concrete task: implement Milestone 1's execution contract and ledger, unify downstream calculations, add accounting/boundary tests, and re-evaluate the saved dataset. No product code changed during this planning task.
