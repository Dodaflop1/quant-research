#!/usr/bin/env python3
"""Fit Hawkes processes to Kalshi trade arrivals, both ways.

Every fit is reported **naive and deseasonalised**, because the difference
between them is the result. On simulated arrivals with zero self-excitation, a
constant-baseline fit reports a branching ratio of 0.79 purely by absorbing a
varying rate (`scripts/hawkes_recovery.py`, study 3). So a single number here
would be uninterpretable: what matters is how much of it survives the
correction.

Three things are enforced rather than left to the reader:

1. **Windowed fitting.** Long series are fitted in windows of a fixed event
   count and the distribution of the branching ratio is reported, not a single
   number. A 90-day series is not stationary, and one fit over the whole span
   silently averages regimes. It is also the only tractable option on the fast
   arm - 3.56M events through a multi-start optimiser is hours.
2. **A held-out Poisson comparison.** A Hawkes fit that cannot beat a constant
   rate on data it was not fitted to has demonstrated nothing, whatever its
   in-sample branching ratio says.
3. **Both goodness-of-fit tests.** KS sees the marginal distribution, Ljung-Box
   sees the autocorrelation. KS alone is how a bad fit passes validation.

    # slow arm: the pinned election/Fed panel
    python scripts/fit_diffusion.py --data ./data --min-trades 2000

    # fast arm: the exchange-wide tape
    python scripts/fit_diffusion.py --data ./data --min-trades 50000 \
        --window-events 20000 --period 3600

    # one market, whole series in a single fit
    python scripts/fit_diffusion.py --data ./data --ticker KXGOVCA-26-XBEC \
        --window-events 0
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.diffusion.hawkes.baseline import (  # noqa: E402
    SECONDS_PER_DAY,
    deseasonalise,
)
from quant.diffusion.hawkes.diagnostics import check_fit  # noqa: E402
from quant.diffusion.hawkes.preprocess import (  # noqa: E402
    aggregate_simultaneous,
    format_tie_report,
    tie_report,
)
from quant.diffusion.hawkes.model import (  # noqa: E402
    fit,
    log_likelihood_on_interval,
    poisson_log_likelihood_on_interval,
)
from quant.ingest.trade_backfill import parse_trade_files  # noqa: E402

log = logging.getLogger("fit")


def load_by_market(data_dir: Path) -> dict[str, np.ndarray]:
    """Trade arrival times per market, in seconds, sorted."""
    paths = sorted((data_dir / "raw").glob("kalshi_trades_*.jsonl"))
    if not paths:
        raise SystemExit(f"no trade files in {data_dir / 'raw'}")

    per_market: dict[str, list[float]] = defaultdict(list)
    for trade in parse_trade_files(paths):
        per_market[trade.contract_id].append(trade.timestamp.timestamp())
    return {t: np.sort(np.asarray(v, dtype=float)) for t, v in per_market.items()}


def fit_one_window(
    times: np.ndarray, T: float, holdout: float
) -> dict | None:
    """Fit, diagnose, and score against Poisson on a held-out tail."""
    if len(times) < 50:
        return None

    split = T * (1.0 - holdout)
    train = times[times <= split]
    if len(train) < 25 or len(times) - len(train) < 5:
        return None

    try:
        params = fit(train, T=split, compute_std_errors=True)
    except (RuntimeError, ValueError) as exc:
        log.debug("fit failed: %s", exc)
        return None
    if not params.converged:
        return None

    # Held-out likelihood. The full history conditions the intensity; only the
    # summation range is the test window. A Poisson rate estimated on the same
    # training data is the null.
    hawkes_ll = log_likelihood_on_interval(
        times, params.mu, params.alpha, params.beta, split, T
    )
    poisson_rate = len(train) / split if split > 0 else 0.0
    poisson_ll = poisson_log_likelihood_on_interval(times, poisson_rate, split, T)
    n_test = int(np.sum(times > split))

    try:
        diag = check_fit(train, params)
        ks_p, lb_p, passes = diag.ks.p_value, diag.ljung_box.p_value, diag.passes
    except (ValueError, RuntimeError):
        ks_p = lb_p = float("nan")
        passes = False

    return {
        "n_events": len(times),
        "n_train": len(train),
        "n_test": n_test,
        "branching_ratio": params.branching_ratio,
        "mu": params.mu,
        "alpha": params.alpha,
        "beta": params.beta,
        "half_life": params.excitation_half_life,
        "se_alpha": params.std_errors.get("alpha"),
        "se_beta": params.std_errors.get("beta"),
        "ks_p": ks_p,
        "ljung_box_p": lb_p,
        "diagnostics_pass": passes,
        "holdout_hawkes_ll": hawkes_ll,
        "holdout_poisson_ll": poisson_ll,
        "holdout_ll_gain": hawkes_ll - poisson_ll,
        "holdout_ll_gain_per_event": (
            (hawkes_ll - poisson_ll) / n_test if n_test else float("nan")
        ),
    }


def fit_series(
    times: np.ndarray,
    window_events: int,
    holdout: float,
    period: float,
    bins: int,
    deseason: bool,
) -> dict:
    """Fit a market's arrivals, optionally deseasonalised, in windows."""
    t0 = float(times[0])
    rel = times - t0
    T = float(rel[-1])
    if T <= 0:
        return {"windows": [], "note": "zero-length series"}

    profile_note = "naive (no seasonality correction)"
    if deseason:
        rel, T, profile = deseasonalise(rel, T, period=period, n_bins=bins)
        profile_note = profile.describe()

    if window_events and len(rel) > window_events:
        edges = list(range(0, len(rel), window_events))
        chunks = [rel[a : a + window_events] for a in edges]
        chunks = [c for c in chunks if len(c) >= 50]
    else:
        chunks = [rel]

    results = []
    for chunk in chunks:
        # Each window is re-based to its own origin. A window starting at
        # t=80 days with a baseline fitted from zero would attribute 80 days of
        # absent history to the baseline.
        local = chunk - chunk[0]
        local_T = float(local[-1])
        out = fit_one_window(local, local_T, holdout)
        if out:
            results.append(out)

    return {"windows": results, "note": profile_note}


def summarise(windows: list[dict], key: str) -> dict:
    vals = np.array([w[key] for w in windows if np.isfinite(w.get(key, np.nan))])
    if not len(vals):
        return {}
    return {
        "n": len(vals),
        "median": float(np.median(vals)),
        "mean": float(vals.mean()),
        "q25": float(np.percentile(vals, 25)),
        "q75": float(np.percentile(vals, 75)),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--data", type=Path, default=Path("./data"))
    p.add_argument("--ticker", action="append", default=[], help="restrict; repeatable")
    p.add_argument("--min-trades", type=int, default=2000,
                   help="skip markets thinner than this (validation showed clean "
                        "recovery at ~2,000 events)")
    p.add_argument("--window-events", type=int, default=5000,
                   help="events per fitting window; 0 fits the whole series at once")
    p.add_argument("--holdout", type=float, default=0.25,
                   help="tail fraction of each window held out for the Poisson test")
    p.add_argument("--period", type=float, default=SECONDS_PER_DAY,
                   help="seasonality period in seconds (default 1 day)")
    p.add_argument("--bins", type=int, default=24, help="phase bins per period")
    p.add_argument(
        "--merge-window",
        type=float,
        default=0.001,
        help=(
            "seconds within which trade prints are treated as ONE order "
            "arrival. A single aggressive order matching several resting "
            "orders emits several prints at the same instant; fitting those as "
            "separate arrivals drives beta to infinity and every window pins "
            "the bound. 0 merges only exact ties; negative disables merging"
        ),
    )
    p.add_argument(
        "--ties",
        action="store_true",
        help="report the gap structure per market and exit, without fitting",
    )
    p.add_argument("--out", type=Path,
                   default=Path("./results/diffusion/hawkes_fits.json"))
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    print(f"loading trades from {args.data / 'raw'} ...")
    by_market = load_by_market(args.data)
    wanted = {
        t: v
        for t, v in by_market.items()
        if len(v) >= args.min_trades and (not args.ticker or t in args.ticker)
    }
    if not wanted:
        print(f"no market has >= {args.min_trades:,} trades")
        return 1

    print(f"{len(wanted)} market(s) of {len(by_market)} clear {args.min_trades:,} trades\n")

    if args.ties:
        print("GAP STRUCTURE (choose --merge-window from this)\n")
        for ticker in sorted(wanted, key=lambda t: -len(wanted[t])):
            print(f"{ticker}  ({len(wanted[ticker]):,} prints)")
            print(format_tie_report(tie_report(wanted[ticker])))
        print("\nAn exact-tie fraction above a percent or two means the raw")
        print("series cannot be fitted directly, whatever the optimiser says.")
        return 0

    if args.merge_window >= 0:
        merged = {}
        rates = []
        for ticker, times in wanted.items():
            agg = aggregate_simultaneous(times, tolerance=args.merge_window)
            merged[ticker] = agg.times
            rates.append(agg.merge_rate)
        if rates:
            print(
                f"aggregated prints into orders at {args.merge_window:g}s: "
                f"median {np.median(rates):.1%} merged, "
                f"max {np.max(rates):.1%}"
            )
        # Re-apply the threshold to ORDERS, not prints. Merging removed a third
        # to two thirds of the records, so a market that cleared 2,000 prints
        # can sit well under the event count the recovery study actually
        # supports - one fell to 630. Filtering before the merge quietly admits
        # fits the validation does not cover.
        thin = {t: len(v) for t, v in merged.items() if len(v) < args.min_trades}
        if thin:
            print(
                f"  {len(thin)} market(s) fell below {args.min_trades:,} ORDERS "
                f"after merging and are excluded: "
                + ", ".join(f"{t} ({n:,})" for t, n in sorted(thin.items(), key=lambda kv: kv[1])[:6])
                + ("..." if len(thin) > 6 else "")
            )
        wanted = {
            t: v for t, v in merged.items()
            if len(v) >= max(args.min_trades, 50)
        }
        print()
        if not wanted:
            print(f"nothing clears {args.min_trades:,} orders after merging. "
                  f"Lower --min-trades if you accept the wider intervals.")
            return 1
    print(f"{'market':<38}{'events':>8}{'naive n':>10}{'deseas n':>10}"
          f"{'shrink':>9}{'half-life':>11}{'vs Pois':>10}{'diag':>6}")
    print("-" * 102)

    results = {}
    for ticker in sorted(wanted, key=lambda t: -len(wanted[t])):
        times = wanted[ticker]
        naive = fit_series(times, args.window_events, args.holdout,
                           args.period, args.bins, deseason=False)
        deseas = fit_series(times, args.window_events, args.holdout,
                            args.period, args.bins, deseason=True)

        n_naive = summarise(naive["windows"], "branching_ratio")
        n_deseas = summarise(deseas["windows"], "branching_ratio")
        if not n_naive or not n_deseas:
            print(f"{ticker:<38}{len(times):>8,}   no converged windows")
            continue

        hl = summarise(deseas["windows"], "half_life")
        gain = summarise(deseas["windows"], "holdout_ll_gain_per_event")
        passes = sum(1 for w in deseas["windows"] if w["diagnostics_pass"])

        shrink = n_naive["median"] - n_deseas["median"]
        print(
            f"{ticker:<38}{len(times):>8,}{n_naive['median']:>10.3f}"
            f"{n_deseas['median']:>10.3f}{shrink:>+9.3f}"
            f"{hl.get('median', float('nan')):>10.0f}s"
            f"{gain.get('median', float('nan')):>10.3f}"
            f"{passes:>4}/{len(deseas['windows'])}"
        )

        results[ticker] = {
            "n_events": int(len(times)),
            "naive": {"summary": n_naive, "windows": naive["windows"],
                      "note": naive["note"]},
            "deseasonalised": {"summary": n_deseas, "windows": deseas["windows"],
                               "note": deseas["note"]},
            "half_life_seconds": hl,
            "holdout_ll_gain_per_event": gain,
            "windows_passing_diagnostics": passes,
            "windows_total": len(deseas["windows"]),
        }

    if not results:
        print("\nnothing converged")
        return 1

    naive_all = [r["naive"]["summary"]["median"] for r in results.values()]
    deseas_all = [r["deseasonalised"]["summary"]["median"] for r in results.values()]
    # Median of the per-market DIFFERENCES, not the difference of the medians.
    # Those are different statistics and the second one lied: on the first live
    # run it reported +0.145 where the median per-market shrink was +0.004, a
    # factor of 35, because the two medians came from different markets.
    shrinks = [n - d for n, d in zip(naive_all, deseas_all)]
    gains = [
        r["holdout_ll_gain_per_event"].get("median", float("nan"))
        for r in results.values()
    ]
    gains = [g for g in gains if np.isfinite(g)]
    beat = sum(1 for g in gains if g > 0)

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"  markets fitted            {len(results)}")
    print(f"  naive branching ratio     median {np.median(naive_all):.3f}")
    print(f"  deseasonalised            median {np.median(deseas_all):.3f}")
    print(f"  median per-market shrink  {np.median(shrinks):+.3f}"
          f"   (range {min(shrinks):+.3f} to {max(shrinks):+.3f})")
    print(f"  beats Poisson held-out    {beat}/{len(gains)} markets")
    print()
    print("  Read the shrinkage first. The seasonality study showed a")
    print("  constant-baseline fit inventing n = 0.79 from a varying rate with")
    print("  no self-excitation at all, so the deseasonalised column is the one")
    print("  that can be quoted. The noise floor from the Poisson negative")
    print("  control is 0.19 - anything below that is not evidence.")
    print()
    print("  A market that does not beat Poisson out of sample has not")
    print("  demonstrated self-excitation, whatever its branching ratio says.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "min_trades": args.min_trades,
            "window_events": args.window_events,
            "holdout": args.holdout,
            "period_seconds": args.period,
            "bins": args.bins,
        },
        "noise_floor_from_negative_control": 0.19,
        "median_per_market_shrink": float(np.median(shrinks)),
        "markets": results,
    }, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
