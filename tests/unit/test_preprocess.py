"""Tests for aggregating trade prints into order arrivals.

Motivated by a live failure. The first fit of the Kalshi slow arm pinned the
``beta`` upper bound in every window of every market, with values up to 3.8e6
per second - an implied excitation half-life around 1e-7 seconds. Cause: a
single aggressive order matching several resting orders emits several prints at
the same instant, and an exponential kernel has no way to put mass at zero lag,
so it drives ``beta`` to infinity instead.

Reproduced below on data with a known answer, where fitting raw prints returns
n = 0.664 against a truth of 0.500 **and reports convergence** - which is what
makes it dangerous rather than merely wrong.
"""

from __future__ import annotations

import numpy as np
import pytest

from quant.diffusion.hawkes.model import fit
from quant.diffusion.hawkes.preprocess import (
    aggregate_simultaneous,
    tie_report,
)
from quant.diffusion.hawkes.simulation import simulate_cluster


def split_into_prints(orders, seed=5, max_fills=6, spread=2e-5):
    """Turn order arrivals into the prints an exchange would report."""
    rng = np.random.default_rng(seed)
    out = []
    for t in orders:
        out.extend(t + rng.uniform(0, spread, size=rng.integers(1, max_fills)))
    return np.sort(np.asarray(out))


# -- aggregation ------------------------------------------------------------


def test_exact_ties_collapse_to_one_event():
    times = np.array([1.0, 1.0, 1.0, 5.0, 9.0, 9.0])
    agg = aggregate_simultaneous(times, tolerance=0.0)
    np.testing.assert_allclose(agg.times, [1.0, 5.0, 9.0])
    np.testing.assert_allclose(agg.multiplicity, [3, 1, 2])
    assert agg.n_prints == 6
    assert agg.merge_rate == pytest.approx(0.5)


def test_the_kept_time_is_the_first_of_the_group():
    """The order arrived when its first fill printed, not its last."""
    agg = aggregate_simultaneous(np.array([1.0, 1.0005, 1.0009]), tolerance=0.001)
    assert agg.times[0] == pytest.approx(1.0)


def test_merging_is_anchored_not_chained():
    """A dense burst must not collapse into a single arrival.

    Chaining on consecutive gaps would merge 1.0, 1.001, 1.002, ... into one
    event however long the run continued, destroying exactly the clustering the
    model exists to measure. Each bucket is anchored to its opening time.
    """
    times = np.array([1.0, 1.0008, 1.0016, 1.0024, 1.0032])
    agg = aggregate_simultaneous(times, tolerance=0.001)
    assert agg.n_events > 1, "anchored merging must not swallow a whole burst"
    np.testing.assert_allclose(agg.times, [1.0, 1.0016, 1.0032])


def test_zero_tolerance_keeps_distinct_times_apart():
    times = np.array([1.0, 1.0000001, 2.0])
    assert aggregate_simultaneous(times, tolerance=0.0).n_events == 3


def test_widely_spaced_events_are_untouched():
    times = np.array([1.0, 100.0, 5000.0])
    agg = aggregate_simultaneous(times, tolerance=0.001)
    np.testing.assert_allclose(agg.times, times)
    assert agg.merge_rate == 0.0


def test_multiplicity_sums_to_the_print_count():
    """Every print lands in exactly one bucket — none dropped, none double-counted."""
    prints = split_into_prints(np.sort(np.random.default_rng(1).uniform(0, 500, 400)))
    agg = aggregate_simultaneous(prints, tolerance=0.001)
    assert agg.multiplicity.sum() == len(prints) == agg.n_prints


def test_output_is_sorted_and_strictly_increasing():
    prints = split_into_prints(np.sort(np.random.default_rng(2).uniform(0, 500, 300)))
    agg = aggregate_simultaneous(prints, tolerance=0.001)
    assert np.all(np.diff(agg.times) > 0)


def test_unsorted_input_is_rejected_rather_than_silently_mangled():
    with pytest.raises(ValueError, match="sorted"):
        aggregate_simultaneous(np.array([3.0, 1.0, 2.0]))


def test_empty_input_is_handled():
    agg = aggregate_simultaneous(np.array([]))
    assert agg.n_events == 0 and agg.merge_rate == 0.0


# -- the tie report ---------------------------------------------------------


def test_tie_report_detects_heavy_sub_millisecond_structure():
    prints = split_into_prints(np.sort(np.random.default_rng(3).uniform(0, 500, 400)))
    rep = tie_report(prints)
    assert rep["under_1ms"] > 0.4
    assert rep["min_positive_gap"] < 1e-3


def test_tie_report_is_clean_on_well_separated_events():
    rep = tie_report(np.arange(0.0, 1000.0, 10.0))
    assert rep["exact_ties"] == 0.0
    assert rep["under_1s"] == 0.0
    assert rep["median_gap"] == pytest.approx(10.0)


def test_tie_report_needs_two_events():
    assert tie_report(np.array([1.0])) == {}


# -- the failure this all exists for ---------------------------------------


def test_fitting_raw_prints_inflates_the_branching_ratio():
    """The live failure, reproduced against a known truth.

    The raw fit does not merely err — it reports success, returns a plausible
    branching ratio, and attaches a microsecond half-life that would pass
    unexamined.
    """
    T = 8000.0
    orders, _ = simulate_cluster(0.5, 0.8, 1.6, T, seed=77)
    prints = split_into_prints(orders)

    raw = fit(prints, T=T, compute_std_errors=False)
    assert raw.branching_ratio > 0.6, "expected the raw fit to be inflated"
    assert raw.excitation_half_life < 1e-3, (
        "expected a physically absurd half-life from the raw fit"
    )


def test_aggregating_recovers_the_truth():
    T = 8000.0
    orders, _ = simulate_cluster(0.5, 0.8, 1.6, T, seed=77)
    prints = split_into_prints(orders)

    agg = aggregate_simultaneous(prints, tolerance=0.001)
    fitted = fit(agg.times, T=T, compute_std_errors=False)

    assert agg.n_events == pytest.approx(len(orders), rel=0.02)
    assert fitted.branching_ratio == pytest.approx(0.5, abs=0.05)
    assert fitted.beta == pytest.approx(1.6, rel=0.15)
    assert fitted.excitation_half_life == pytest.approx(np.log(2) / 1.6, rel=0.15)


def test_aggregation_is_harmless_when_there_are_no_ties():
    """Applied to a clean series the correction must change nothing material."""
    T = 6000.0
    orders, _ = simulate_cluster(0.5, 0.8, 1.6, T, seed=78)

    direct = fit(orders, T=T, compute_std_errors=False)
    agg = aggregate_simultaneous(orders, tolerance=0.001)
    viaagg = fit(agg.times, T=T, compute_std_errors=False)

    assert agg.merge_rate < 0.01
    assert viaagg.branching_ratio == pytest.approx(direct.branching_ratio, abs=0.02)
