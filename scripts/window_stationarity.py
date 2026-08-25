#!/usr/bin/env python3
"""Is the arrival rate stable inside each fitting window?

Study E showed that a 2x rate step manufactures a branching ratio of 0.80 out of
data with no self-excitation at all, and that the time-rescaling diagnostics pass
on it 24 times out of 24. That made every high branching ratio in the panel
uninterpretable until this question is answered. It establishes *sufficiency*.
This measures whether the condition actually holds.

The test
--------
Naively one would bin the window and ask whether the counts look constant. That
is wrong here: **self-excitation itself produces uneven bin counts.** A genuine
Hawkes process clusters, so a dispersion test calibrated against Poisson would
reject on every window and prove nothing.

So the null is not Poisson, it is *the fitted Hawkes itself*:

1. Fit the window, giving `(mu, alpha, beta)`.
2. Simulate `B` series from exactly those parameters over the same `T`.
3. Compute the same dispersion statistic on each.
4. The p-value is the fraction of simulations at least as dispersed as the real
   window.

A small p-value means the window varies more than its own fitted model can
account for - the rate is moving for reasons the Hawkes is not capturing, which
is precisely the Study E condition. A large p-value means the observed
clustering is consistent with the self-excitation that was fitted.

This is a parametric bootstrap, so it inherits the fit's assumptions. It cannot
prove stationarity; it can only fail to detect a departure.

The statistic
-------------
Index of dispersion of counts in `K` equal-*time* bins: `var / mean`. Equal time,
not equal counts - the whole question is whether time is being filled evenly.
Chosen over max/min because one empty bin should not dominate, and over a
trend test because the departure need not be monotone.

Usage:
    python scripts/window_stationarity.py --data ./data \\
        --out results/diffusion/window_stationarity.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from quant.diffusion.hawkes import model as exp_model            # noqa: E402
from quant.diffusion.hawkes.diagnostics import check_fit          # noqa: E402
from quant.diffusion.hawkes.preprocess import aggregate_simultaneous  # noqa: E402
from quant.diffusion.hawkes.simulation import simulate_thinning  # noqa: E402
from fit_diffusion import load_by_market                         # noqa: E402

log = logging.getLogger("window_stationarity")
BASE_SEED = 20260825

# From Study E: what a step of this size does to a fitted branching ratio.
STEP_REFERENCE = {1.0: 0.034, 2.0: 0.799, 4.0: 0.942, 8.0: 0.975, 16.0: 0.989}


def dispersion(times: np.ndarray, T: float, bins: int) -> float:
    """Index of dispersion of counts in equal-time bins. 1.0 under Poisson."""
    counts, _ = np.histogram(times, bins=bins, range=(0.0, T))
    mean = counts.mean()
    return float(counts.var() / mean) if mean > 0 else float("nan")


def halves_ratio(times: np.ndarray, T: float) -> float:
    """Rate in the busier half over the quieter half - the Study E axis."""
    first = int(np.sum(times < T / 2.0))
    second = len(times) - first
    lo, hi = sorted((max(first, 1), max(second, 1)))
    return hi / lo


@dataclass
class WindowResult:
    ticker: str
    index: int
    n_events: int
    span_hours: float
    n_hat: float | None
    half_life_seconds: float | None
    mu_hat: float | None
    converged: bool
    dispersion: float | None = None
    dispersion_null_median: float | None = None
    dispersion_null_p95: float | None = None
    p_value: float | None = None
    halves_ratio: float | None = None
    halves_ratio_null_p95: float | None = None
    diagnostics_pass: bool | None = None


def analyse_window(ticker: str, idx: int, times: np.ndarray, bins: int,
                   n_sims: int, seed: int) -> WindowResult:
    local = times - times[0]
    T = float(local[-1])
    span_h = T / 3600.0
    if T <= 0 or len(local) < 50:
        return WindowResult(ticker, idx, len(local), span_h, None, None, None, False)

    p = exp_model.fit(local, T=T, compute_std_errors=False)
    base = WindowResult(
        ticker, idx, len(local), span_h,
        p.branching_ratio if p.converged else None,
        p.excitation_half_life if p.converged else None,
        p.mu if p.converged else None,
        p.converged,
    )
    if not p.converged:
        return base

    try:
        base.diagnostics_pass = bool(check_fit(local, p).passes)
    except Exception as exc:
        log.warning("diagnostics failed for %s w%d: %s", ticker, idx, exc)

    observed = dispersion(local, T, bins)
    observed_ratio = halves_ratio(local, T)

    null_d, null_r = [], []
    for b in range(n_sims):
        sim = simulate_thinning(p.mu, p.alpha, p.beta, T, seed=seed + b)
        if len(sim) < 2:
            continue
        null_d.append(dispersion(sim, T, bins))
        null_r.append(halves_ratio(sim, T))

    if not null_d:
        return base
    null_d_arr = np.asarray(null_d)
    base.dispersion = observed
    base.dispersion_null_median = float(np.median(null_d_arr))
    base.dispersion_null_p95 = float(np.quantile(null_d_arr, 0.95))
    # +1 in numerator and denominator: a bootstrap p-value of exactly zero
    # claims more resolution than B simulations can support.
    base.p_value = float((np.sum(null_d_arr >= observed) + 1) / (len(null_d_arr) + 1))
    base.halves_ratio = observed_ratio
    base.halves_ratio_null_p95 = float(np.quantile(np.asarray(null_r), 0.95))
    return base


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", type=Path, default=Path("./data"))
    ap.add_argument("--min-trades", type=int, default=2000)
    ap.add_argument("--window-events", type=int, default=5000)
    ap.add_argument("--merge-window", type=float, default=0.001)
    ap.add_argument("--bins", type=int, default=20)
    ap.add_argument("--sims", type=int, default=200)
    ap.add_argument("--out", type=Path,
                    default=Path("results/diffusion/window_stationarity.json"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    raw = load_by_market(args.data)
    merged = {t: aggregate_simultaneous(v, tolerance=args.merge_window).times
              for t, v in raw.items()}
    markets = {t: v for t, v in merged.items() if len(v) >= args.min_trades}
    log.info("%d markets clear %d orders after merging at %gs",
             len(markets), args.min_trades, args.merge_window)

    results: list[WindowResult] = []
    for m_idx, ticker in enumerate(sorted(markets, key=lambda t: -len(markets[t]))):
        times = markets[ticker]
        if args.window_events and len(times) > args.window_events:
            chunks = [times[a:a + args.window_events]
                      for a in range(0, len(times), args.window_events)]
            chunks = [c for c in chunks if len(c) >= 50]
        else:
            chunks = [times]
        for w_idx, chunk in enumerate(chunks):
            seed = BASE_SEED + 1000 * m_idx + 10 * w_idx
            r = analyse_window(ticker, w_idx, chunk, args.bins, args.sims, seed)
            results.append(r)
            log.info("%-32s w%d  n=%s  disp %s vs null %s (p=%s)  halves %s",
                     ticker, w_idx, _f(r.n_hat), _f(r.dispersion, 1),
                     _f(r.dispersion_null_median, 1), _f(r.p_value), _f(r.halves_ratio, 2))

    ok = [r for r in results if r.p_value is not None]
    flagged = [r for r in ok if r.p_value < 0.05]
    high = [r for r in ok if (r.n_hat or 0) >= 0.7]
    high_flagged = [r for r in high if r.p_value < 0.05]

    print("\n" + "=" * 96)
    print(f"{'market':<32} {'w':>2} {'events':>7} {'n':>6} {'half-life':>10} "
          f"{'disp':>8} {'null':>7} {'p':>7} {'halves':>7} {'diag':>5}")
    for r in results:
        print(f"{r.ticker:<32} {r.index:>2} {r.n_events:>7,} {_f(r.n_hat):>6} "
              f"{_f(r.half_life_seconds, 0):>10} {_f(r.dispersion, 1):>8} "
              f"{_f(r.dispersion_null_median, 1):>7} {_f(r.p_value):>7} "
              f"{_f(r.halves_ratio, 2):>7} "
              f"{'pass' if r.diagnostics_pass else 'REJ' if r.diagnostics_pass is False else '-':>5}")

    # Does the branching ratio track the rate variation? This is the question
    # Study E poses and only real data can answer.
    pairs = [(r.halves_ratio, r.n_hat) for r in ok
             if r.halves_ratio is not None and r.n_hat is not None]
    corr = (float(np.corrcoef([a for a, _ in pairs], [b for _, b in pairs])[0, 1])
            if len(pairs) > 2 else float("nan"))

    # Cross-tabulate stationarity against the time-rescaling diagnostics. Study E
    # predicts these are INDEPENDENT: a rate step inflates n while the
    # diagnostics pass. If they coincide here, the real departure is not a clean
    # step and something else is also wrong.
    tab = {"flag_and_reject": 0, "flag_and_pass": 0, "clean_and_reject": 0,
           "clean_and_pass": 0}
    for r in ok:
        if r.diagnostics_pass is None:
            continue
        key = ("flag" if r.p_value < 0.05 else "clean") + \
              ("_and_pass" if r.diagnostics_pass else "_and_reject")
        tab[key] += 1

    print("\nVERDICT")
    print(f"  {len(flagged)}/{len(ok)} windows vary more than their own fitted Hawkes "
          f"can account for (p < 0.05).")
    if high:
        print(f"  Of the {len(high)} windows with n >= 0.7, {len(high_flagged)} are "
              f"flagged.")
    if flagged:
        ratios = [r.halves_ratio for r in flagged if r.halves_ratio]
        if ratios:
            print(f"  Flagged windows show half-to-half rate ratios of "
                  f"{min(ratios):.2f}x to {max(ratios):.2f}x.")
            print(f"  Study E reference: a 2x step alone produces n = "
                  f"{STEP_REFERENCE[2.0]}, a 4x step n = {STEP_REFERENCE[4.0]}.")
        print("  Branching ratios from flagged windows cannot be read as "
              "self-excitation.")
    else:
        print("  No window departs from its fitted model. The Study E mechanism is "
              "available but is not what is happening here, and the high mode needs "
              "another explanation.")

    print(f"\n  corr(half-to-half rate ratio, fitted n) = {corr:+.3f} over "
          f"{len(pairs)} windows.")
    print("  Study E predicts this correlation if the rate variation is driving n.")
    print(f"\n  Stationarity flag vs time-rescaling diagnostics:")
    print(f"    flagged & diagnostics reject   {tab['flag_and_reject']:>3}")
    print(f"    flagged & diagnostics pass     {tab['flag_and_pass']:>3}")
    print(f"    clean   & diagnostics reject   {tab['clean_and_reject']:>3}")
    print(f"    clean   & diagnostics pass     {tab['clean_and_pass']:>3}")

    payload = {
        "config": {"bins": args.bins, "sims": args.sims,
                   "window_events": args.window_events,
                   "merge_window": args.merge_window, "base_seed": BASE_SEED},
        "study_e_reference": STEP_REFERENCE,
        "windows": [asdict(r) for r in results],
        "summary": {
            "corr_halves_ratio_vs_n": corr,
            "stationarity_vs_diagnostics": tab,
            "windows_tested": len(ok),
            "flagged_p05": len(flagged),
            "high_n_windows": len(high),
            "high_n_flagged": len(high_flagged),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=float), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


def _f(v, places: int = 3) -> str:
    return "n/a" if v is None or (isinstance(v, float) and not np.isfinite(v)) \
        else f"{v:.{places}f}"


if __name__ == "__main__":
    raise SystemExit(main())
