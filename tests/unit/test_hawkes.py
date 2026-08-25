"""Tests for the Hawkes simulator, estimator and diagnostics.

The estimator's real validation is the replication study in
``scripts/hawkes_recovery.py``; these tests cover the properties that must hold
on every call, and the specific failures already encountered.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from quant.common.db.schema import HawkesParameters
from quant.diffusion.hawkes.diagnostics import check_fit, rescale
from quant.diffusion.hawkes.model import (
    _decay_states,
    compensator,
    fit,
    log_likelihood,
)
from quant.diffusion.hawkes.simulation import simulate_cluster, simulate_thinning


# -- simulation -------------------------------------------------------------


def test_cluster_immigrant_fraction_matches_theory():
    """A fraction 1 - n of events should be immigrants."""
    mu, alpha, beta, T = 1.0, 0.6, 2.0, 30_000.0
    times, parents = simulate_cluster(mu, alpha, beta, T, seed=1)
    immigrant_fraction = float(np.mean(parents < 0))
    assert immigrant_fraction == pytest.approx(1.0 - alpha / beta, abs=0.02)


def test_cluster_rate_matches_theory():
    """Stationary rate is mu / (1 - n)."""
    mu, alpha, beta, T = 0.4, 0.5, 2.0, 40_000.0
    times, _ = simulate_cluster(mu, alpha, beta, T, seed=2)
    expected = mu / (1.0 - alpha / beta)
    assert len(times) / T == pytest.approx(expected, rel=0.05)


def test_cluster_parent_indices_are_remapped_after_sorting():
    """Parents must point at the post-sort position, and always backwards.

    The construction appends events out of order and sorts at the end. If the
    parent indices are not remapped through that sort they silently name the
    wrong ancestor, which no summary statistic would reveal.
    """
    times, parents = simulate_cluster(1.0, 0.5, 2.0, 2_000.0, seed=3)
    assert len(times) > 100
    assert np.all(np.diff(times) >= 0)
    for child, parent in enumerate(parents):
        if parent >= 0:
            assert parent < child, "a parent must precede its child after sorting"
            assert times[parent] <= times[child]


def test_cluster_rejects_supercritical():
    with pytest.raises(ValueError, match="branching ratio"):
        simulate_cluster(1.0, 2.0, 1.0, 100.0, seed=4)


def test_thinning_agrees_with_cluster_on_rate():
    """Two independent algorithms should produce the same stationary rate."""
    mu, alpha, beta, T = 0.5, 0.6, 1.5, 20_000.0
    thinned = simulate_thinning(mu, alpha, beta, T, seed=5)
    clustered, _ = simulate_cluster(mu, alpha, beta, T, seed=5)
    assert len(thinned) / T == pytest.approx(len(clustered) / T, rel=0.08)


def test_thinning_handles_supercritical_without_the_cluster_restriction():
    """Thinning has no stationarity requirement; it just gets busy."""
    times = simulate_thinning(0.1, 1.2, 1.0, 50.0, seed=6, max_events=200_000)
    assert len(times) > 0
    assert np.all(np.diff(times) >= 0)


def test_simulation_is_reproducible():
    a, _ = simulate_cluster(1.0, 0.5, 2.0, 500.0, seed=42)
    b, _ = simulate_cluster(1.0, 0.5, 2.0, 500.0, seed=42)
    np.testing.assert_array_equal(a, b)


# -- likelihood -------------------------------------------------------------


def test_decay_recursion_matches_the_naive_quadratic_sum():
    """The O(n) recursion is the whole reason this scales. Pin it down."""
    times = np.sort(np.random.default_rng(7).uniform(0, 100, size=200))
    beta = 1.3
    fast = _decay_states(times, beta)
    slow = np.array(
        [sum(math.exp(-beta * (t - s)) for s in times[:i]) for i, t in enumerate(times)]
    )
    np.testing.assert_allclose(fast, slow, rtol=1e-10, atol=1e-12)


def test_likelihood_reduces_to_poisson_as_alpha_vanishes():
    """With no excitation the model is Poisson, whose log-likelihood is known."""
    times = np.sort(np.random.default_rng(8).uniform(0, 100, size=150))
    mu, T = 1.5, 100.0
    tiny_alpha = 1e-12
    expected = len(times) * math.log(mu) - mu * T
    assert log_likelihood(times, mu, tiny_alpha, 1.0, T) == pytest.approx(
        expected, rel=1e-6
    )


def test_likelihood_rejects_non_positive_parameters():
    times = np.array([1.0, 2.0, 3.0])
    assert log_likelihood(times, -1.0, 0.5, 1.0, 10.0) == -np.inf
    assert log_likelihood(times, 1.0, -0.5, 1.0, 10.0) == -np.inf
    assert log_likelihood(times, 1.0, 0.5, -1.0, 10.0) == -np.inf


def test_likelihood_is_maximised_near_the_generating_parameters():
    """The truth should beat nearby wrong answers, or MLE is pointless."""
    mu, alpha, beta, T = 0.5, 0.8, 1.6, 5_000.0
    times, _ = simulate_cluster(mu, alpha, beta, T, seed=9)
    at_truth = log_likelihood(times, mu, alpha, beta, T)
    for wrong in ((mu * 3, alpha, beta), (mu, alpha * 3, beta), (mu, alpha, beta * 3)):
        assert log_likelihood(times, *wrong, T) < at_truth


# -- fitting ----------------------------------------------------------------


def test_fit_recovers_generating_parameters():
    mu, alpha, beta, T = 0.5, 0.8, 1.6, 20_000.0
    times, _ = simulate_cluster(mu, alpha, beta, T, seed=10)
    p = fit(times, T=T)
    assert p.converged
    assert p.mu == pytest.approx(mu, rel=0.15)
    assert p.alpha == pytest.approx(alpha, rel=0.15)
    assert p.beta == pytest.approx(beta, rel=0.15)
    assert p.branching_ratio == pytest.approx(alpha / beta, rel=0.10)


def test_fit_reports_near_zero_excitation_on_poisson_data():
    """The negative control, as a test rather than only as a study."""
    rng = np.random.default_rng(11)
    T = 5_000.0
    times = np.sort(rng.uniform(0, T, size=rng.poisson(1.0 * T)))
    p = fit(times, T=T, compute_std_errors=False)
    assert p.branching_ratio < 0.25, (
        f"estimator manufactured excitation (n={p.branching_ratio:.3f}) in "
        "independent arrivals"
    )


def test_fit_does_not_run_away_to_absurd_parameters():
    """Regression: L-BFGS-B walked to alpha ~ 1e190 and reported success.

    Unbounded log-parameters let the optimiser reach the region where exp()
    overflows. The objective there returned a flat penalty, so the gradient was
    zero and the solver declared convergence at a meaningless point. The bug
    was invisible in a single 20,000-event fit and only surfaced as an infinite
    variance across replications at 2,000 events.
    """
    mu, alpha, beta, T = 0.5, 0.8, 1.6, 2_000.0
    for seed in range(15):
        times, _ = simulate_cluster(mu, alpha, beta, T, seed=seed)
        p = fit(times, T=T, compute_std_errors=False)
        assert p.alpha < 1e6, f"seed {seed}: alpha ran away to {p.alpha:g}"
        assert p.beta < 1e6, f"seed {seed}: beta ran away to {p.beta:g}"
        assert 0.0 < p.branching_ratio < 20.0


def test_fit_flags_boundary_solutions_as_not_converged():
    """A solution resting on a bound has no interior optimum to linearise at."""
    # Near-degenerate: two tight bursts, nothing in between.
    times = np.concatenate([np.linspace(0, 0.01, 40), np.linspace(999, 999.01, 40)])
    p = fit(times, T=1000.0, compute_std_errors=True)
    if not p.converged:
        assert p.std_errors == {}, "standard errors quoted on a boundary solution"


def test_fit_requires_at_least_two_events():
    with pytest.raises(ValueError, match="at least 2"):
        fit([1.0], T=10.0)


def test_fit_rejects_unsorted_times():
    with pytest.raises(ValueError, match="sorted"):
        fit([3.0, 1.0, 2.0], T=10.0)


def test_fit_rejects_a_window_that_ends_before_the_last_event():
    with pytest.raises(ValueError, match="ends before"):
        fit([1.0, 2.0, 30.0], T=10.0)


def test_standard_errors_are_present_and_plausible():
    mu, alpha, beta, T = 0.5, 0.8, 1.6, 20_000.0
    times, _ = simulate_cluster(mu, alpha, beta, T, seed=12)
    p = fit(times, T=T, compute_std_errors=True)
    assert set(p.std_errors) == {"mu", "alpha", "beta"}
    for name, value in p.std_errors.items():
        assert value > 0
        # An SE larger than the estimate itself means nothing is identified.
        assert value < getattr(p, name)


# -- derived quantities -----------------------------------------------------


def test_branching_ratio_is_alpha_over_beta_not_something_else():
    """Regression: an earlier draft documented ``alpha * beta / mu``.

    The branching ratio is the integral of the kernel, which for
    ``alpha * exp(-beta * s)`` is ``alpha / beta``. The baseline does not enter
    it at all.
    """
    p = HawkesParameters(
        mu=99.0,  # deliberately large; must not affect the ratio
        alpha=0.6,
        beta=2.0,
        time_unit="second",
        log_likelihood=-1.0,
        n_events=10,
        observation_window=100.0,
        converged=True,
    )
    assert p.branching_ratio == pytest.approx(0.3)
    assert p.is_stationary
    assert p.excitation_half_life == pytest.approx(math.log(2) / 2.0)
    assert p.expected_cluster_size == pytest.approx(1.0 / 0.7)


def test_supercritical_parameters_have_no_expected_cluster_size():
    p = HawkesParameters(
        mu=1.0,
        alpha=3.0,
        beta=2.0,
        time_unit="second",
        log_likelihood=-1.0,
        n_events=10,
        observation_window=100.0,
        converged=True,
    )
    assert not p.is_stationary
    assert p.expected_cluster_size is None


# -- diagnostics ------------------------------------------------------------


def test_rescale_matches_the_direct_compensator_difference():
    """The recursion must equal Lambda(t_k) - Lambda(t_{k-1}) computed directly."""
    times = np.sort(np.random.default_rng(13).uniform(0, 200, size=300))
    mu, alpha, beta = 0.7, 0.9, 1.4
    fast = rescale(times, mu, alpha, beta)
    direct = np.diff(
        np.concatenate(
            [[0.0], [compensator(times, mu, alpha, beta, t) for t in times]]
        )
    )
    np.testing.assert_allclose(fast, direct, rtol=1e-9, atol=1e-10)


def test_diagnostics_accept_a_correctly_specified_fit():
    mu, alpha, beta, T = 0.5, 0.8, 1.6, 20_000.0
    times, _ = simulate_cluster(mu, alpha, beta, T, seed=14)
    p = fit(times, T=T, compute_std_errors=False)
    d = check_fit(times, p)
    assert d.intervals.mean() == pytest.approx(1.0, abs=0.05)
    assert d.intervals.var() == pytest.approx(1.0, abs=0.15)
    assert not d.ks.rejects_null
    assert d.passes


def test_diagnostics_reject_badly_wrong_parameters():
    """A test that never rejects is not a test."""
    times, _ = simulate_cluster(0.5, 0.8, 1.6, 20_000.0, seed=15)
    wrong = HawkesParameters(
        mu=5.0,
        alpha=0.1,
        beta=50.0,
        time_unit="second",
        log_likelihood=-1.0,
        n_events=len(times),
        observation_window=20_000.0,
        converged=True,
    )
    d = check_fit(times, wrong)
    assert d.ks.rejects_null
    assert not d.passes


def test_uniforms_are_the_exponential_cdf_of_the_intervals():
    times, _ = simulate_cluster(0.5, 0.8, 1.6, 2_000.0, seed=16)
    p = fit(times, T=2_000.0, compute_std_errors=False)
    d = check_fit(times, p)
    np.testing.assert_allclose(d.uniforms, 1.0 - np.exp(-d.intervals), rtol=1e-12)
    assert np.all((d.uniforms >= 0) & (d.uniforms <= 1))


def test_passes_requires_both_tests_not_just_ks():
    """Conjunctive by design: unit-exponential but autocorrelated is a failure."""
    times, _ = simulate_cluster(0.5, 0.8, 1.6, 5_000.0, seed=17)
    p = fit(times, T=5_000.0, compute_std_errors=False)
    d = check_fit(times, p)
    assert d.passes == (not d.ks.rejects_null and not d.ljung_box.rejects_null)
