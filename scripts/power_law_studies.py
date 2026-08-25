#!/usr/bin/env python3
"""Validation studies B, C and D for the power-law (Omori) kernel.

Study A (recovery) is already covered by
``tests/unit/test_power_law.py::test_recovers_the_branching_ratio`` and by the
table in ``docs/power_law_review.md``. This script is the remaining three.

--------------------------------------------------------------------------
IMPLEMENTATION CONTRACT — read before writing any code
--------------------------------------------------------------------------

Everything outside the three ``run_study_*`` functions is finished: the CLI,
the seeding, the result types, the JSON writer and the markdown report all
work. Do not restructure them. Fill in the three function bodies marked
``NotImplementedError`` and nothing else.

Rules, all of which have already cost this project time when broken:

1.  **Every random draw takes an explicit seed.** A simulation study whose
    numbers cannot be reproduced is an anecdote. Seeds are derived from
    ``BASE_SEED`` plus the replication index so a rerun is bit-identical.

2.  **Never fit with ``T=None``.** It defaults to the last event time, which
    is a downward-biased window. Always pass the real ``T``.

3.  **``compute_std_errors=False`` inside sweep loops.** It costs ~36 extra
    likelihood evaluations per fit. Turn it on only where the standard error
    is itself the reported quantity (Study B does not need it; Study D does,
    for ``eps``).

4.  **A non-converged fit is data, not an error.** Record it and carry on.
    ``fit()`` and ``fit_power_law()`` both report boundary solutions as
    ``converged=False``. Count them; do not silently drop them, and do not
    let them into the medians.

5.  **Report the spread, not just the centre.** Every summary carries the
    median and the 10th/90th percentiles across replications. The single most
    important thing Study A taught us is that ``eps`` had a threefold spread
    across four seeds while its median looked fine.

Run:  python scripts/power_law_studies.py --studies B C D --out results/diffusion/power_law_studies.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.diffusion.hawkes import model as exp_model          # noqa: E402
from quant.diffusion.hawkes.diagnostics import check_fit       # noqa: E402
from quant.diffusion.hawkes.power_law import (                 # noqa: E402
    fit_power_law,
    simulate_power_law,
)
from quant.diffusion.hawkes.simulation import simulate_thinning  # noqa: E402

log = logging.getLogger("power_law_studies")

BASE_SEED = 20260825


# ---------------------------------------------------------------------------
# result types
# ---------------------------------------------------------------------------


@dataclass
class Replication:
    """One simulated series, fitted. ``None`` fields mean the fit did not
    converge — keep the row so the failure rate is visible."""

    seed: int
    truth: dict[str, float]
    n_events: int
    converged: bool
    n_hat: float | None = None
    half_life: float | None = None
    eps_hat: float | None = None
    eps_se: float | None = None
    ks_p: float | None = None
    ljung_p: float | None = None
    diagnostics_pass: bool | None = None


@dataclass
class StudyResult:
    name: str
    description: str
    replications: list[Replication] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    verdict: str = ""


def percentiles(values: list[float]) -> dict[str, float]:
    """Median with a 10-90 band. Empty input gives NaNs rather than raising."""
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    if arr.size == 0:
        return {"p10": float("nan"), "median": float("nan"), "p90": float("nan"), "count": 0}
    return {
        "p10": float(np.quantile(arr, 0.10)),
        "median": float(np.median(arr)),
        "p90": float(np.quantile(arr, 0.90)),
        "count": int(arr.size),
    }


def diagnostics_of(times: np.ndarray, params, alpha_level: float = 0.05):
    """Time-rescaling diagnostics for an *exponential* fit.

    Both tests must pass. KS alone is not enough: a process can match the
    marginal distribution of rescaled intervals while leaving serial structure
    in them, which is exactly what misspecification looks like.

    Returns ``(ks_p, ljung_p, passed)``.
    """
    res = check_fit(times, params, alpha_level=alpha_level)
    return float(res.ks.p_value), float(res.ljung_box.p_value), bool(res.passes)


# ---------------------------------------------------------------------------
# Study B — negative control
# ---------------------------------------------------------------------------


def run_study_b(n_reps: int = 20, T: float = 4000.0, rate: float = 0.6) -> StudyResult:
    """Poisson data must give a branching ratio near zero.

    WHAT TO DO
    ----------
    For each replication ``i`` in ``range(n_reps)``:
      - seed = BASE_SEED + i
      - draw a homogeneous Poisson process on [0, T] at intensity ``rate``.
        (`rng.exponential(1/rate)` cumulative sums, truncated at T — or
        equivalently draw ``rng.poisson(rate*T)`` uniforms and sort. Either is
        fine; pick one and keep it.)
      - fit with ``fit_power_law(times, T=T, compute_std_errors=False)``
      - record ``n_hat``; there is no true ``eps`` here so leave it None

    WHAT TO REPORT
    --------------
    ``summary["noise_floor"]`` = the 95th percentile of ``n_hat`` across
    replications. This is the number the study exists to produce: any branching
    ratio at or below it on real data is indistinguishable from noise.

    PASS CRITERIA
    -------------
    - median ``n_hat`` < 0.15
    - the 95th percentile is the noise floor; state it, do not judge it

    WHY IT MATTERS
    --------------
    The exponential kernel's floor is **0.19** — measured, not assumed. There
    is no reason the power-law kernel's should match, and the fast/slow contrast
    in the real fits (n ~ 0.88 vs n ~ 0.23) is only meaningful relative to it.
    If this floor comes out near 0.23, the slow arm is noise and the write-up
    has to say so.

    Set ``verdict`` to a one-line statement including the floor.
    """
    raise NotImplementedError("Study B")


# ---------------------------------------------------------------------------
# Study C — the key experiment
# ---------------------------------------------------------------------------

STUDY_C_EPS_GRID = (0.5, 1.0, 1.5, 2.5, 4.0)


def run_study_c(
    n_reps: int = 10,
    T: float = 4000.0,
    mu: float = 0.30,
    n_true: float = 0.50,
    tau: float = 1.0,
) -> StudyResult:
    """Fit an EXPONENTIAL kernel to simulated POWER-LAW data.

    This is the experiment the whole power-law detour exists to run.

    WHAT TO DO
    ----------
    For each ``eps`` in ``STUDY_C_EPS_GRID``, for each replication ``i``:
      - seed = BASE_SEED + 1000*grid_index + i
      - ``times = simulate_power_law(mu, n_true, tau, eps, T, seed=seed)``
      - fit the WRONG model:
        ``params = exp_model.fit(times, T=T, compute_std_errors=False)``
      - record ``n_hat = params.branching_ratio``,
        ``half_life = params.excitation_half_life``,
        and the diagnostics via ``diagnostics_of(times, params)``
      - ``truth`` should carry the eps that generated the series, so the
        replications can be grouped by it afterwards

    WHAT TO REPORT
    --------------
    ``summary["by_eps"]`` keyed by eps, each holding:
      - ``n_hat`` percentiles
      - ``half_life`` percentiles
      - ``diagnostic_rejection_rate`` — fraction with ``diagnostics_pass`` False
      - ``non_convergence_rate``

    THE COMPARISON THAT MATTERS
    ---------------------------
    On the real slow arm we observe, from 9 markets over 18 windows:

      - **bimodal branching ratio**: 4 markets cluster at n ~ 0.88 with
        excitation half-lives of 975-6221 s, and 5 at n ~ 0.23 with half-lives
        of 11-1849 s
      - **only 5 of 18 windows pass** the time-rescaling diagnostics
      - all 9 markets beat a Poisson held-out benchmark

    The question this study answers: **does fitting an exponential kernel to
    power-law data reproduce that pattern?** Specifically —

      (a) does ``n_hat`` become bimodal across the eps sweep, or across
          replications within a single eps?
      (b) does high ``n_hat`` travel with long ``half_life``, as it does in the
          real data?
      (c) is the diagnostic rejection rate comparable to 13/18 (72%)?

    If yes to all three, the real data's misspecification is *demonstrated*
    rather than asserted, and that reasoning stands on its own regardless of
    what the power-law fit returns on real data. If no, the bimodality is a
    property of the markets and needs a different explanation — which is an
    equally publishable outcome, so do not tune the study until it says yes.

    Set ``verdict`` to a direct answer on (a), (b) and (c) with the numbers.
    """
    raise NotImplementedError("Study C")


# ---------------------------------------------------------------------------
# Study D — the reverse
# ---------------------------------------------------------------------------


def run_study_d(
    n_reps: int = 10,
    T: float = 4000.0,
    mu: float = 0.30,
    alpha: float = 0.5,
    beta: float = 1.0,
) -> StudyResult:
    """Fit the POWER-LAW kernel to EXPONENTIAL data.

    Guards against the new kernel simply fitting everything.

    WHAT TO DO
    ----------
    For each replication ``i``:
      - seed = BASE_SEED + 2000 + i
      - ``times = simulate_thinning(mu, alpha, beta, T, seed=seed)``
        (true branching ratio is ``alpha / beta`` = 0.5)
      - ``params = fit_power_law(times, T=T, compute_std_errors=True)``
        — standard errors ON here, because ``eps_se`` is a reported quantity
      - record ``n_hat``, ``eps_hat``, ``eps_se``

    WHAT TO REPORT
    --------------
      - ``n_hat`` percentiles against the truth of ``alpha/beta``
      - ``eps_hat`` percentiles
      - ``fraction_eps_identified`` — how often
        ``PowerLawParameters.eps_is_identified`` is True

    PASS CRITERIA
    -------------
      - median ``n_hat`` within 0.10 of ``alpha/beta``: the wrong kernel must
        not invent excitation that is not there
      - median ``eps_hat`` LARGE (say > 2). A power law with a heavy tail index
        approaches an exponential, so this is the correct way for the model to
        say "no long memory here". A small ``eps`` on exponential data would
        mean the kernel manufactures long memory on demand, which would
        invalidate any long-memory claim it makes on real data.

    Note the asymmetry with Study A: there ``eps`` was unidentified at ~2,400
    events (se 22-54%). Expect it to be unidentified here too. That is fine —
    the claim being tested is directional (``eps`` large, not ``eps`` precise),
    and ``fraction_eps_identified`` is what records the caveat.

    Set ``verdict`` to a one-line statement of whether the kernel invented
    long memory.
    """
    raise NotImplementedError("Study D")


# ---------------------------------------------------------------------------
# driver — finished, do not modify
# ---------------------------------------------------------------------------

STUDIES = {
    "B": ("negative control - Poisson data must give n near zero", run_study_b),
    "C": ("the key experiment - exponential kernel on power-law data", run_study_c),
    "D": ("the reverse - power-law kernel on exponential data", run_study_d),
}


def format_report(results: list[StudyResult]) -> str:
    lines = ["# Power-law validation studies", ""]
    for r in results:
        converged = sum(1 for x in r.replications if x.converged)
        lines += [
            f"## Study {r.name} — {r.description}",
            "",
            f"Replications: {len(r.replications)} ({converged} converged)",
            "",
            "```json",
            json.dumps(r.summary, indent=2, default=float),
            "```",
            "",
            f"**Verdict:** {r.verdict or '(not set)'}",
            "",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--studies", nargs="+", default=["B", "C", "D"], choices=list(STUDIES))
    ap.add_argument("--reps", type=int, default=None, help="override replication count")
    ap.add_argument("--out", type=Path, default=Path("results/diffusion/power_law_studies.json"))
    ap.add_argument("--report", type=Path, default=None, help="also write a markdown report")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    results: list[StudyResult] = []
    for key in args.studies:
        description, fn = STUDIES[key]
        log.info("running Study %s — %s", key, description)
        kwargs = {"n_reps": args.reps} if args.reps else {}
        result = fn(**kwargs)
        result.name, result.description = key, description
        results.append(result)
        log.info("Study %s: %s", key, result.verdict or "(no verdict set)")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps([asdict(r) for r in results], indent=2, default=float), encoding="utf-8"
    )
    log.info("wrote %s", args.out)

    report = format_report(results)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        log.info("wrote %s", args.report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
