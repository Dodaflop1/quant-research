"""Turning trade prints into a point process that can actually be fitted.

A single aggressive order that matches against several resting orders produces
several trade records at the same instant. Those are not several arrivals; they
are one arrival reported in pieces, and the distinction is fatal rather than
cosmetic.

An exponential-kernel Hawkes process has no way to represent mass at zero lag.
Faced with events separated by microseconds it does the only thing it can:
drive ``beta`` toward infinity so the excitation is tall enough and brief enough
to cover the tie. The first live fit of the Kalshi slow arm did exactly this -
**every window in every market** hit the ``beta`` upper bound, with values up to
3.8e6 per second, an implied excitation half-life around 1e-7 seconds. The
branching ratios that came with those fits were not obviously absurd, which is
what makes the failure dangerous: without the boundary check they would have
been reported.

So trades are aggregated into orders before fitting. The mark - how many prints
an order consumed - is preserved, because it is the natural size measure for a
marked extension later.

The tolerance is a judgement call and is therefore reported rather than
hidden. :func:`tie_report` prints the gap structure so the choice can be made
from the data instead of assumed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AggregatedEvents:
    """Order arrivals recovered from trade prints."""

    times: np.ndarray
    """One time per aggregated order, sorted."""

    multiplicity: np.ndarray
    """Prints consumed by each order. The natural mark for a marked model."""

    n_prints: int
    tolerance: float

    @property
    def n_events(self) -> int:
        return len(self.times)

    @property
    def merge_rate(self) -> float:
        """Fraction of prints that were absorbed into an earlier one.

        This is a fact about the exchange's reporting, not a tuning artefact,
        and it belongs in the write-up: it says how much of the apparent
        arrival rate is order flow and how much is fill reporting.
        """
        if self.n_prints == 0:
            return 0.0
        return 1.0 - self.n_events / self.n_prints

    def describe(self) -> str:
        return (
            f"{self.n_prints:,} prints -> {self.n_events:,} orders "
            f"({self.merge_rate:.1%} merged at {self.tolerance:g}s), "
            f"max {int(self.multiplicity.max())} prints/order"
        )


def aggregate_simultaneous(
    times: Sequence[float] | np.ndarray, tolerance: float = 0.001
) -> AggregatedEvents:
    """Collapse prints within ``tolerance`` seconds into single arrivals.

    Greedy and forward-only: each event opens a bucket, and every subsequent
    print within ``tolerance`` **of that bucket's opening time** joins it. The
    alternative - chaining on consecutive gaps - would let a dense burst merge
    into one arrival however long it ran, which would destroy exactly the
    clustering the model is meant to measure.

    ``tolerance=0`` merges only exact ties.
    """
    times = np.asarray(times, dtype=float)
    if len(times) == 0:
        return AggregatedEvents(times, np.empty(0), 0, tolerance)
    if np.any(np.diff(times) < 0):
        raise ValueError("times must be sorted ascending")

    keep_idx: list[int] = [0]
    counts: list[int] = [1]
    anchor = times[0]
    for i in range(1, len(times)):
        if times[i] - anchor <= tolerance:
            counts[-1] += 1
        else:
            keep_idx.append(i)
            counts.append(1)
            anchor = times[i]

    return AggregatedEvents(
        times=times[np.asarray(keep_idx)],
        multiplicity=np.asarray(counts, dtype=float),
        n_prints=len(times),
        tolerance=tolerance,
    )


def tie_report(times: Sequence[float] | np.ndarray) -> dict:
    """Describe the gap structure, so a tolerance is chosen from evidence.

    The number to look at is the exact-tie fraction. Anything above a percent
    or two means the raw series cannot be fitted directly, whatever the
    optimiser reports.
    """
    times = np.asarray(times, dtype=float)
    if len(times) < 2:
        return {}
    gaps = np.diff(times)
    n = len(gaps)
    buckets = {
        "exact_ties": float(np.sum(gaps == 0) / n),
        "under_1ms": float(np.sum(gaps < 1e-3) / n),
        "under_10ms": float(np.sum(gaps < 1e-2) / n),
        "under_100ms": float(np.sum(gaps < 1e-1) / n),
        "under_1s": float(np.sum(gaps < 1.0) / n),
    }
    positive = gaps[gaps > 0]
    buckets["min_positive_gap"] = float(positive.min()) if len(positive) else 0.0
    buckets["median_gap"] = float(np.median(gaps))
    return buckets


def format_tie_report(report: dict) -> str:
    if not report:
        return "  (too few events)"
    return (
        f"  exact ties {report['exact_ties']:.1%}  "
        f"<1ms {report['under_1ms']:.1%}  "
        f"<1s {report['under_1s']:.1%}  "
        f"min gap {report['min_positive_gap']:.4g}s  "
        f"median gap {report['median_gap']:.1f}s"
    )
