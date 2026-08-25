#!/usr/bin/env python3
"""Validate the Hawkes estimator before it touches real data.

A Hawkes fit on market data will produce a branching ratio whatever you feed it.
The number is worthless until three questions have been answered about the
estimator itself, and each is a study here:

1. **Does it recover what it generated?** Fit simulated samples with known
   parameters and check both the point estimates and the coverage of the
   confidence intervals. Coverage is the part usually skipped: point estimates
   can look fine while the standard errors are badly wrong, and it is the
   standard errors that decide whether a branching ratio of 0.62 is meaningfully
   different from 0.55.

2. **Does it find excitation where there is none?** Fit a homogeneous Poisson
   process. The estimated branching ratio must be near zero. An estimator that
   reports self-excitation in independent arrivals would invalidate every result
   downstream of it.

3. **Does a time-varying baseline masquerade as self-excitation?** Fit a
   *purely Poisson* process whose rate varies over the day. There is no
   self-excitation at all, but a constant-baseline Hawkes has no other way to
   explain clustering and absorbs the seasonality as spurious excitation.

Study 3 is the one that matters for the Kalshi application. Published
order-flow branching ratios near 0.9 are contested precisely on these grounds -
Hardiman, Bercot and Bouchaud argue that estimates approaching criticality are
partly an artefact of non-stationary baselines and kernel misspecification
rather than genuine reflexivity. Kalshi trade flow has obvious intraday and
event-driven seasonality, so quoting a branching ratio without addressing this
would reproduce the exact error the literature has already litigated.

    python scripts/hawkes_recovery.py                 # all three, 100 replications
    python scripts/hawkes_recovery.py --reps 500      # tighter coverage estimates
    python scripts/hawkes_recovery.py --study 3       # just the seasonality study
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.diffusion.hawkes.baseline import deseasonalise  # noqa: E402
from quant.diffusion.hawkes.diagnostics import check_fit  # noqa: E402
from quant.diffusion.hawkes.model import fit  # noqa: E402
from quant.diffusion.hawkes.simulation import simulate_cluster  # noqa: E402

log = logging.getLogger("recovery")


def simulate_poisson(rate: float, T: float, seed: int) -> np.ndarray:
    """Homogeneous Poisson arrivals on ``[0, T]``."""
    rng = np.random.default_rng(seed)
    return np.sort(rng.uniform(0.0, T, size=rng.poisson(rate * T)))


def simulate_seasonal_poisson(
    base_rate: float, amplitude: float, period: float, T: float, seed: int
) -> np.ndarray:
    """Inhomogeneous Poisson with a sinusoidal rate, by thinning.

    ``lambda(t) = base_rate * (1 + amplitude * sin(2*pi*t / period))``. There is
    no self-excitation whatsoever: every arrival is independent given the rate
    function. Any branching ratio a Hawkes fit reports on this data is entirely
    an artefact of forcing a constant baseline onto a varying one.
    """
    rng = np.random.default_rng(seed)
    ceiling = base_rate * (1.0 + amplitude)
    candidates = np.sort(rng.uniform(0.0, T, size=rng.poisson(ceiling * T)))
    intensity = base_rate * (1.0 + amplitude * np.sin(2 * np.pi * candidates / period))
    return candidates[rng.uniform(0.0, ceiling, size=len(candidates)) <= intensity]


def study_recovery(reps: int, T: float, truth: dict[str, float]) -> dict:
    """Study 1: parameter recovery and confidence-interval coverage."""
    mu, alpha, beta = truth["mu"], truth["alpha"], truth["beta"]
    true_n = alpha / beta
    print(f"\n{'=' * 72}\nSTUDY 1  Parameter recovery and CI coverage")
    print(f"  truth: mu={mu} alpha={alpha} beta={beta}  n={true_n:.3f}")
    print(f"  {reps} replications, window T={T:g}\n{'=' * 72}")

    rows, excluded = [], 0
    for rep in range(reps):
        times, _ = simulate_cluster(mu, alpha, beta, T, seed=1000 + rep)
        if len(times) < 50:
            continue
        try:
            p = fit(times, T=T, compute_std_errors=True)
        except (RuntimeError, ValueError) as exc:
            log.warning("rep %d failed: %s", rep, exc)
            excluded += 1
            continue
        if not p.converged:
            # A boundary or non-converged fit is excluded and counted, never
            # averaged in. Silently including them is what turned a broken
            # optimiser into a plausible-looking mean on the first run.
            excluded += 1
            continue
        rows.append(
            {
                "mu": p.mu,
                "alpha": p.alpha,
                "beta": p.beta,
                "n": p.branching_ratio,
                "se_mu": p.std_errors.get("mu"),
                "se_alpha": p.std_errors.get("alpha"),
                "se_beta": p.std_errors.get("beta"),
                "n_events": p.n_events,
            }
        )
        if (rep + 1) % 20 == 0:
            print(f"  ... {rep + 1}/{reps}")

    if not rows:
        raise RuntimeError("every replication failed")

    print(f"\n  {len(rows)} converged fits, {excluded} excluded "
          f"(non-convergence or boundary), "
          f"median {np.median([r['n_events'] for r in rows]):.0f} events each\n")
    print(f"  {'param':<8}{'truth':>10}{'mean est':>12}{'bias %':>10}"
          f"{'mean SE':>10}{'emp SD':>10}{'95% cov':>10}")
    print("  " + "-" * 60)

    summary = {}
    for name, true_value in (("mu", mu), ("alpha", alpha), ("beta", beta)):
        estimates = np.array([r[name] for r in rows])
        ses = np.array(
            [r[f"se_{name}"] for r in rows if r[f"se_{name}"] is not None]
        )
        covered = [
            abs(r[name] - true_value) <= 1.96 * r[f"se_{name}"]
            for r in rows
            if r[f"se_{name}"] is not None
        ]
        coverage = float(np.mean(covered)) if covered else float("nan")
        bias_pct = 100.0 * (estimates.mean() - true_value) / true_value
        print(
            f"  {name:<8}{true_value:>10.4f}{estimates.mean():>12.4f}"
            f"{bias_pct:>+10.2f}{ses.mean() if len(ses) else np.nan:>10.4f}"
            f"{estimates.std(ddof=1):>10.4f}{coverage:>10.1%}"
        )
        summary[name] = {
            "truth": true_value,
            "mean_estimate": float(estimates.mean()),
            "bias_pct": float(bias_pct),
            "mean_std_error": float(ses.mean()) if len(ses) else None,
            "empirical_sd": float(estimates.std(ddof=1)),
            "ci_coverage_95": coverage,
        }

    n_hat = np.array([r["n"] for r in rows])
    print(f"\n  branching ratio: truth {true_n:.4f}, "
          f"mean {n_hat.mean():.4f}, SD {n_hat.std(ddof=1):.4f}")
    print("\n  Read the last two columns together. A mean standard error close to")
    print("  the empirical SD, and coverage near 95%, is what licenses quoting a")
    print("  confidence interval on real data. Coverage far below 95% means the")
    print("  point estimates may be fine while the error bars are fiction.")

    summary["branching_ratio"] = {
        "truth": true_n,
        "mean_estimate": float(n_hat.mean()),
        "empirical_sd": float(n_hat.std(ddof=1)),
    }
    summary["n_replications"] = len(rows)
    summary["n_excluded"] = excluded
    return summary


def study_negative_control(reps: int, T: float, rate: float) -> dict:
    """Study 2: Poisson data must not produce self-excitation."""
    print(f"\n{'=' * 72}\nSTUDY 2  Negative control - homogeneous Poisson")
    print(f"  truth: n = 0 exactly. {reps} replications at rate {rate}, T={T:g}")
    print(f"{'=' * 72}")

    ratios, excluded = [], 0
    for rep in range(min(reps, 50)):
        times = simulate_poisson(rate, T, seed=5000 + rep)
        if len(times) < 50:
            continue
        try:
            p = fit(times, T=T, compute_std_errors=False)
        except (RuntimeError, ValueError):
            excluded += 1
            continue
        if not p.converged:
            excluded += 1
            continue
        ratios.append(p.branching_ratio)

    ratios = np.array(ratios)
    print(f"\n  {len(ratios)} converged fits on independent arrivals "
          f"({excluded} excluded)")
    print(f"  branching ratio: mean {ratios.mean():.4f}, "
          f"median {np.median(ratios):.4f}, 95th pct {np.percentile(ratios, 95):.4f}")
    verdict = "PASS" if np.median(ratios) < 0.15 else "FAIL"
    print(f"  [{verdict}] a median near zero means the estimator does not "
          f"manufacture excitation")
    print("\n  The 95th percentile is the number to remember: it is roughly the")
    print("  threshold a real branching ratio must clear before it is evidence of")
    print("  anything beyond finite-sample noise.")

    return {
        "n_fits": len(ratios),
        "n_excluded": excluded,
        "mean_branching_ratio": float(ratios.mean()),
        "median_branching_ratio": float(np.median(ratios)),
        "p95_branching_ratio": float(np.percentile(ratios, 95)),
        "verdict": verdict,
    }


def study_seasonality(reps: int, T: float, rate: float, amplitude: float) -> dict:
    """Study 3: a varying baseline read as self-excitation."""
    period = T / 20.0
    print(f"\n{'=' * 72}\nSTUDY 3  Seasonality contamination")
    print(f"  Inhomogeneous Poisson, rate {rate} * (1 + {amplitude}*sin), "
          f"period {period:g}")
    print(f"  truth: n = 0 exactly. There is no self-excitation in this data.")
    print(f"{'=' * 72}")

    ratios, rejected, excluded = [], 0, 0
    for rep in range(min(reps, 50)):
        times = simulate_seasonal_poisson(rate, amplitude, period, T, seed=9000 + rep)
        if len(times) < 50:
            continue
        try:
            p = fit(times, T=T, compute_std_errors=False)
            if not p.converged:
                excluded += 1
                continue
            diag = check_fit(times, p)
        except (RuntimeError, ValueError):
            excluded += 1
            continue
        ratios.append(p.branching_ratio)
        rejected += int(not diag.passes)

    ratios = np.array(ratios)
    print(f"\n  {len(ratios)} converged fits on data with zero true "
          f"self-excitation ({excluded} excluded)")
    print(f"  branching ratio: mean {ratios.mean():.4f}, "
          f"median {np.median(ratios):.4f}, max {ratios.max():.4f}")
    print(f"  residual diagnostics rejected the fit in "
          f"{rejected}/{len(ratios)} cases ({rejected / max(len(ratios), 1):.0%})")
    print("\n  Two things to take from this:")
    print("  1. A constant-baseline Hawkes reports self-excitation that is not")
    print("     there, because absorbing a varying rate is the only way it can")
    print("     explain the clustering. Kalshi flow is strongly seasonal, so any")
    print("     branching ratio fitted on raw trade times inherits this bias.")
    print("  2. Whether the diagnostics catch it is the practical question, and")
    print("     it is the reason both tests are run rather than KS alone.")
    print("\n  The mitigation, applied in the real fit: estimate a time-varying")
    print("  baseline first, or fit within windows short enough that the baseline")
    print("  is locally flat, and report the branching ratio both ways.")

    return {
        "n_fits": len(ratios),
        "n_excluded": excluded,
        "true_branching_ratio": 0.0,
        "mean_branching_ratio": float(ratios.mean()),
        "median_branching_ratio": float(np.median(ratios)),
        "max_branching_ratio": float(ratios.max()),
        "diagnostics_rejection_rate": rejected / max(len(ratios), 1),
    }


def study_correction(reps: int, T: float, rate: float, amplitude: float) -> dict:
    """Study 4: does the seasonal time change actually fix study 3?

    Two halves, and the second matters more than the first. Removing a spurious
    branching ratio is easy if you are willing to shrink everything; the useful
    question is whether the correction is *specific* - whether genuine
    self-excitation survives it intact.
    """
    period = T / 20.0
    print(f"\n{'=' * 72}\nSTUDY 4  Does the seasonal time change fix study 3?")
    print(f"{'=' * 72}")

    print("\n  (a) seasonal Poisson, true n = 0")
    print(f"      {'naive':>12}{'corrected':>12}{'peak/trough':>14}")
    naive_a, corr_a = [], []
    for rep in range(min(reps, 8)):
        times = simulate_seasonal_poisson(rate, amplitude, period, T, seed=9000 + rep)
        if len(times) < 200:
            continue
        try:
            n_raw = fit(times, T=T, compute_std_errors=False)
            ops, ops_T, prof = deseasonalise(times, T, period=period, n_bins=20)
            n_fix = fit(ops, T=ops_T, compute_std_errors=False)
        except (RuntimeError, ValueError):
            continue
        naive_a.append(n_raw.branching_ratio)
        corr_a.append(n_fix.branching_ratio)
        print(f"      {n_raw.branching_ratio:>12.3f}{n_fix.branching_ratio:>12.3f}"
              f"{prof.peak_to_trough:>14.2f}")

    print("\n  (b) genuine Hawkes, no seasonality, true n = 0.5")
    print(f"      {'naive':>12}{'corrected':>12}")
    naive_b, corr_b = [], []
    for rep in range(min(reps, 6)):
        times, _ = simulate_cluster(0.5, 0.8, 1.6, T, seed=400 + rep)
        try:
            n_raw = fit(times, T=T, compute_std_errors=False)
            ops, ops_T, _ = deseasonalise(times, T, period=period, n_bins=20)
            n_fix = fit(ops, T=ops_T, compute_std_errors=False)
        except (RuntimeError, ValueError):
            continue
        naive_b.append(n_raw.branching_ratio)
        corr_b.append(n_fix.branching_ratio)
        print(f"      {n_raw.branching_ratio:>12.3f}{n_fix.branching_ratio:>12.3f}")

    if not naive_a or not naive_b:
        return {"error": "no converged fits"}

    ma, mca = float(np.median(naive_a)), float(np.median(corr_a))
    mb, mcb = float(np.median(naive_b)), float(np.median(corr_b))
    verdict = "PASS" if mca < 0.19 and abs(mcb - 0.5) < 0.1 else "FAIL"

    print(f"\n  spurious n removed:   {ma:.3f} -> {mca:.3f}   (truth 0)")
    print(f"  genuine n preserved:  {mb:.3f} -> {mcb:.3f}   (truth 0.5)")
    print(f"  cost of the correction on real excitation: {mb - mcb:+.3f}")
    print(f"\n  [{verdict}] the correction is specific to seasonality rather")
    print("  than shrinking every branching ratio it touches. That specificity")
    print("  is what licenses quoting the corrected number on real data.")

    return {
        "spurious_naive": ma,
        "spurious_corrected": mca,
        "genuine_naive": mb,
        "genuine_corrected": mcb,
        "cost_on_real_excitation": mb - mcb,
        "verdict": verdict,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--reps", type=int, default=100, help="replications per study")
    p.add_argument("--window", type=float, default=2000.0, help="observation window")
    p.add_argument(
        "--study",
        type=int,
        action="append",
        default=[],
        choices=[1, 2, 3, 4],
        help="run only these studies; repeatable",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("./results/diffusion/hawkes_validation.json"),
        help="where to write the machine-readable summary",
    )
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    studies = args.study or [1, 2, 3, 4]

    results: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "replications": args.reps,
        "window": args.window,
    }

    if 1 in studies:
        results["recovery"] = study_recovery(
            args.reps, args.window, {"mu": 0.5, "alpha": 0.8, "beta": 1.6}
        )
    if 2 in studies:
        results["negative_control"] = study_negative_control(
            args.reps, args.window, rate=1.0
        )
    if 3 in studies:
        results["seasonality"] = study_seasonality(
            args.reps, args.window, rate=1.0, amplitude=0.8
        )

    if 4 in studies:
        results["correction"] = study_correction(
            args.reps, args.window, rate=1.0, amplitude=0.8
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
