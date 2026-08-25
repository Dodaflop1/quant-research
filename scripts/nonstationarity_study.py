#!/usr/bin/env python3
"""Study E: can a rate step alone produce the slow arm's signature?

Study C ruled out power-law misspecification. The remaining candidates for the
real data's bimodal branching ratio and 72% diagnostic rejection are residual
seasonality, remaining ties, genuine heterogeneity in `n`, and **non-stationarity
within the fitting window**. This tests the last one, which is the only candidate
with a plausible route to `n ~ 0.88`.

The mechanism, stated before measuring it
-----------------------------------------
A Hawkes fit assumes a constant baseline. Give it a series whose rate steps up
partway through the window and it has two ways to explain the extra events: raise
`mu`, or attribute them to self-excitation. Self-excitation wins whenever the
step is persistent, because a constant `mu` cannot produce a *sustained* burst
but a slow-decaying kernel can. The prediction is therefore:

- `n` inflates toward 1 as the step grows, with **no self-excitation present at
  all**
- the fitted half-life becomes **long** - the kernel is standing in for a regime
  that lasts half the window, not for a burst
- the time-rescaling diagnostics reject, because the residuals carry the step

That combination - high `n`, long half-life, diagnostic rejection - is exactly
what the real slow arm shows, and exactly what Study C could not reproduce.

Two arms
--------
- **E1, pure Poisson with a step.** Truth is `n = 0`. Anything above the noise
  floor of 0.19 (exponential kernel, measured) is manufactured by the step.
- **E2, genuine Hawkes with a step.** Truth is `n = 0.3`. Measures how much a
  step inflates a real branching ratio, which is the case the real data is in
  if these markets self-excite at all.

Scale is chosen to match the real fits: `T` of one day and ~2,500 events per
window, so the fitted half-lives are comparable to the real 975-6221 s rather
than to a toy unit scale.

Usage:
    python scripts/nonstationarity_study.py --reps 12 \\
        --out results/diffusion/nonstationarity_study.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.diffusion.hawkes import model as exp_model            # noqa: E402
from quant.diffusion.hawkes.diagnostics import check_fit         # noqa: E402
from quant.diffusion.hawkes.simulation import simulate_thinning  # noqa: E402

log = logging.getLogger("nonstationarity")

BASE_SEED = 20260825
T_WINDOW = 86_400.0          # one day, matching the real fitting windows
TARGET_EVENTS = 2_500        # matching the real slow-arm window size
STEP_RATIOS = (1.0, 2.0, 4.0, 8.0, 16.0)
NOISE_FLOOR = 0.19           # exponential kernel, measured on Poisson data

# The real slow arm, for comparison.
REAL_HIGH_MODE = 0.88
REAL_HIGH_HALF_LIVES = (975.0, 6221.0)
REAL_REJECTION = 13 / 18


@dataclass
class Row:
    seed: int
    step_ratio: float
    n_true: float
    n_events: int
    converged: bool
    n_hat: float | None = None
    half_life: float | None = None
    mu_hat: float | None = None
    ks_p: float | None = None
    ljung_p: float | None = None
    passes: bool | None = None


def spread(values: list[float]) -> dict[str, Any]:
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    if arr.size == 0:
        return {"median": None, "p10": None, "p90": None, "count": 0}
    return {"median": float(np.median(arr)), "p10": float(np.quantile(arr, 0.1)),
            "p90": float(np.quantile(arr, 0.9)), "count": int(arr.size)}


def stepped_poisson(rng, rate_before: float, rate_after: float, T: float) -> np.ndarray:
    """Inhomogeneous Poisson with one step at T/2, by conditioning on counts.

    Exact rather than thinned: the number of events in each half is Poisson with
    the appropriate mean, and their positions are uniform within the half. No
    acceptance loop, so no dependence on a proposal bound.
    """
    half = T / 2.0
    a = rng.uniform(0.0, half, size=rng.poisson(rate_before * half))
    b = rng.uniform(half, T, size=rng.poisson(rate_after * half))
    return np.sort(np.concatenate([a, b]))


def stepped_hawkes(seed: int, mu_before: float, mu_after: float,
                   alpha: float, beta: float, T: float) -> np.ndarray:
    """Genuine self-excitation with a baseline step.

    Simulated as two independent halves, the second conditioned on nothing from
    the first. That understates cross-boundary excitation, which makes the test
    CONSERVATIVE: any inflation measured here is a lower bound on what a real
    stepped series would produce.
    """
    half = T / 2.0
    first = simulate_thinning(mu_before, alpha, beta, half, seed=seed)
    second = simulate_thinning(mu_after, alpha, beta, half, seed=seed + 500_000) + half
    return np.sort(np.concatenate([first, second]))


def run_arm(name: str, n_true: float, n_reps: int) -> dict[str, Any]:
    """One arm of the study, swept over step size."""
    rows: list[Row] = []
    by_ratio: dict[str, Any] = {}

    for idx, ratio in enumerate(STEP_RATIOS):
        # Hold the TOTAL event count fixed across ratios. Otherwise a bigger
        # step also means more data, and the two effects are not separable:
        # more events alone tightens the fit and changes the diagnostics.
        mean_rate = TARGET_EVENTS / T_WINDOW
        rate_before = 2.0 * mean_rate / (1.0 + ratio)
        rate_after = rate_before * ratio
        arm_rows: list[Row] = []

        for i in range(n_reps):
            seed = BASE_SEED + 10_000 * idx + i + (0 if n_true == 0 else 100_000)
            rng = np.random.default_rng(seed)
            if n_true == 0.0:
                times = stepped_poisson(rng, rate_before, rate_after, T_WINDOW)
            else:
                # mu*(1/(1-n)) is the observed rate, so scale mu down to hit the
                # same total count as the Poisson arm.
                beta = 1.0 / 600.0                      # 10-minute decay
                alpha = n_true * beta
                times = stepped_hawkes(seed, rate_before * (1 - n_true),
                                       rate_after * (1 - n_true), alpha, beta, T_WINDOW)

            if len(times) < 50:
                arm_rows.append(Row(seed, ratio, n_true, len(times), False))
                continue
            p = exp_model.fit(times, T=T_WINDOW, compute_std_errors=False)
            if not p.converged:
                arm_rows.append(Row(seed, ratio, n_true, len(times), False))
                continue
            res = check_fit(times, p)
            arm_rows.append(Row(
                seed, ratio, n_true, len(times), True,
                n_hat=p.branching_ratio, half_life=p.excitation_half_life, mu_hat=p.mu,
                ks_p=float(res.ks.p_value), ljung_p=float(res.ljung_box.p_value),
                passes=bool(res.passes),
            ))

        ok = [r for r in arm_rows if r.converged]
        rejected = sum(1 for r in ok if r.passes is False)
        by_ratio[f"{ratio:g}"] = {
            "rate_before_per_s": rate_before,
            "rate_after_per_s": rate_after,
            "n_hat": spread([r.n_hat for r in ok]),
            "half_life_seconds": spread([r.half_life for r in ok]),
            "events": spread([float(r.n_events) for r in arm_rows]),
            "diagnostics_rejected": f"{rejected}/{len(ok)}",
            "rejection_rate": rejected / len(ok) if ok else None,
            "above_noise_floor": sum(1 for r in ok if (r.n_hat or 0) > NOISE_FLOOR),
            "non_convergence": 1 - len(ok) / len(arm_rows) if arm_rows else 0.0,
        }
        rows.extend(arm_rows)
        log.info("%s ratio %-4g  n=%s  half-life=%ss  rejected %s", name, ratio,
                 _fmt(by_ratio[f"{ratio:g}"]["n_hat"]["median"]),
                 _fmt(by_ratio[f"{ratio:g}"]["half_life_seconds"]["median"], 0),
                 by_ratio[f"{ratio:g}"]["diagnostics_rejected"])

    return {"n_true": n_true, "by_step_ratio": by_ratio,
            "replications": [asdict(r) for r in rows]}


def _fmt(v, places: int = 3) -> str:
    return "n/a" if v is None else f"{v:.{places}f}"


def verdict(e1: dict, e2: dict) -> str:
    """Does a rate step reproduce high n + long half-life + rejection?"""
    cells = e1["by_step_ratio"]
    biggest = cells[f"{max(STEP_RATIOS):g}"]
    n = biggest["n_hat"]["median"]
    hl = biggest["half_life_seconds"]["median"]
    rej = biggest["rejection_rate"]
    flat = cells[f"{min(STEP_RATIOS):g}"]["n_hat"]["median"]

    if n is None:
        return "no converged fits at the largest step; inconclusive"
    reaches = n >= 0.7
    long_tail = hl is not None and REAL_HIGH_HALF_LIVES[0] <= hl <= REAL_HIGH_HALF_LIVES[1] * 3
    rejects = rej is not None and rej >= 0.5
    parts = [
        f"A {max(STEP_RATIOS):g}x rate step on data with NO self-excitation gives "
        f"n = {n:.3f} (against {flat:.3f} with no step, and a noise floor of "
        f"{NOISE_FLOOR}), half-life {_fmt(hl, 0)}s, diagnostics rejecting "
        f"{biggest['diagnostics_rejected']}.",
        f"Real slow arm: n ~ {REAL_HIGH_MODE}, half-lives "
        f"{REAL_HIGH_HALF_LIVES[0]:.0f}-{REAL_HIGH_HALF_LIVES[1]:.0f}s, "
        f"rejection {REAL_REJECTION:.0%}.",
    ]
    if reaches and long_tail and rejects:
        parts.append("All three signatures reproduced. Non-stationarity within the "
                     "window is a sufficient explanation for the high mode, and the "
                     "windowed fits cannot be read as evidence of reflexivity "
                     "until the rate is shown to be stable within each window.")
    elif reaches or rejects:
        parts.append("Partially reproduced - see which of the three signatures "
                     "appear above. A partial match still means the high mode "
                     "cannot be attributed to self-excitation without a "
                     "stationarity check.")
    else:
        parts.append("Not reproduced. A rate step of this size does not manufacture "
                     "the high mode, which leaves heterogeneity across markets as "
                     "the main remaining candidate.")
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reps", type=int, default=12)
    ap.add_argument("--out", type=Path,
                    default=Path("results/diffusion/nonstationarity_study.json"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    log.info("E1: pure Poisson with a rate step (truth n = 0)")
    e1 = run_arm("E1", 0.0, args.reps)
    log.info("E2: genuine Hawkes with a rate step (truth n = 0.3)")
    e2 = run_arm("E2", 0.3, args.reps)

    v = verdict(e1, e2)
    payload = {"config": {"T": T_WINDOW, "target_events": TARGET_EVENTS,
                          "step_ratios": list(STEP_RATIOS), "reps": args.reps,
                          "base_seed": BASE_SEED},
               "E1_poisson_with_step": e1, "E2_hawkes_with_step": e2, "verdict": v}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=float), encoding="utf-8")

    print("\n" + "=" * 78)
    for name, arm in (("E1  truth n = 0.0", e1), ("E2  truth n = 0.3", e2)):
        print(f"\n{name}")
        print(f"  {'step':>6} {'n_hat':>18} {'half-life (s)':>20} {'rejected':>10}")
        for ratio, cell in arm["by_step_ratio"].items():
            n, hl = cell["n_hat"], cell["half_life_seconds"]
            print(f"  {ratio:>5}x {_fmt(n['median']):>8} "
                  f"[{_fmt(n['p10'])},{_fmt(n['p90'])}] "
                  f"{_fmt(hl['median'], 0):>8} [{_fmt(hl['p10'], 0)},{_fmt(hl['p90'], 0)}]"
                  f" {cell['diagnostics_rejected']:>10}")
    print("\nVERDICT\n  " + v.replace(". ", ".\n  "))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
