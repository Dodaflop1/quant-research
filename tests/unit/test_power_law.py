"""Tests for the power-law (Omori) kernel.

Three of these are regression tests for bugs in the first implementation, all of
which shared a shape: the grid was derived inside each call from whatever slice
of data that call happened to see, so the same parameters produced different
kernels depending on how they were invoked.

The most important test here is `test_approximation_matches_the_closed_form`.
The obvious test — that `sum(c/beta) == n` — is true *by construction* from the
rescaling line, so it passes even if the grid is nonsense. Only comparison
against the closed form can catch a bad discretisation.
"""

from __future__ import annotations

import numpy as np
import pytest

from quant.diffusion.hawkes.power_law import (
    GridBounds,
    PowerLawKernel,
    PowerLawParameters,
    fit_power_law,
    simulate_power_law,
)

WIDE = GridBounds(beta_min=1e-4, beta_max=1e3)


# -- the approximation ------------------------------------------------------


def test_approximation_matches_the_closed_form():
    """The test that can actually catch a bad grid.

    `sum(c/beta) == n` is enforced by the rescaling and proves nothing about
    the discretisation. This compares against
    `phi(t) = n eps tau^eps / (t+tau)^(1+eps)` across four decades of lag.
    """
    k = PowerLawKernel(n=0.5, tau=1.0, eps=1.5, bounds=WIDE)
    t = np.array([0.0, 0.01, 0.1, 1.0, 10.0, 100.0])
    rel = np.abs(k.phi(t) / k.phi_exact(t) - 1.0)
    assert np.max(rel) < 1e-3, f"max relative error {np.max(rel):.2e}"


@pytest.mark.parametrize("eps", [1.0, 1.5, 3.0])
@pytest.mark.parametrize("tau", [0.1, 1.0, 10.0])
def test_approximation_holds_across_the_parameter_range(eps, tau):
    """For eps >= 1 the discretisation is essentially exact at the default M."""
    k = PowerLawKernel(n=0.4, tau=tau, eps=eps, bounds=GridBounds(1e-5, 1e4))
    t = np.array([0.0, tau / 10, tau, tau * 10, tau * 100])
    rel = np.abs(k.phi(t) / k.phi_exact(t) - 1.0)
    assert np.max(rel) < 5e-4


@pytest.mark.parametrize("tau", [0.1, 1.0, 10.0])
def test_heavy_tails_carry_a_bounded_shape_error(tau):
    """eps < 1 has infinite mean lag, so a finite grid truncates its tail.

    More grid points do not help - the error is span, not density - and
    beta_min is fixed by the observation window, which is an identifiability
    limit rather than a defect. Pinned at ~1% so a regression is visible, and
    documented so an eps < 1 result is reported with the caveat.

    The branching ratio is unaffected: exact by construction.
    """
    k = PowerLawKernel(n=0.4, tau=tau, eps=0.5, bounds=GridBounds(1e-5, 1e4))
    t = np.array([0.0, tau / 10, tau, tau * 10, tau * 100])
    rel = np.abs(k.phi(t) / k.phi_exact(t) - 1.0)
    assert np.max(rel) < 2e-2
    assert float(np.sum(k.c / k.beta)) == pytest.approx(0.4, rel=1e-12)


def test_branching_ratio_is_exact_not_approximate():
    """`n` is the reported quantity, so quadrature error is removed from it."""
    for n in (0.1, 0.5, 0.9):
        k = PowerLawKernel(n=n, tau=1.0, eps=1.5, bounds=WIDE)
        assert float(np.sum(k.c / k.beta)) == pytest.approx(n, rel=1e-12)


def test_finer_grid_does_not_move_the_answer():
    """Convergence check: if M matters, M is too small."""
    a = PowerLawKernel(n=0.5, tau=1.0, eps=1.5, bounds=WIDE, m=25)
    b = PowerLawKernel(n=0.5, tau=1.0, eps=1.5, bounds=WIDE, m=60)
    t = np.array([0.05, 0.5, 5.0, 50.0])
    assert np.max(np.abs(a.phi(t) / b.phi(t) - 1.0)) < 5e-3


def test_rejects_non_positive_parameters():
    for bad in (dict(n=-0.5), dict(tau=0.0), dict(eps=-1.5)):
        kwargs = dict(n=0.5, tau=1.0, eps=1.5, bounds=WIDE) | bad
        with pytest.raises(ValueError, match="positive"):
            PowerLawKernel(**kwargs)


# -- regressions: one kernel, one grid --------------------------------------


def test_interval_likelihood_is_additive():
    """REGRESSION. The first version rebuilt the grid per call, so
    `compensator(t_end)` and `compensator(t_start)` used different grids and
    their difference was not an integral over the window. Additivity was off by
    4e-4 — small, but held-out gains on real data run as low as 0.009/event.
    """
    times = np.sort(np.random.default_rng(1).uniform(0, 500, 800))
    T, mid, mu = 500.0, 300.0, 0.5
    k = PowerLawKernel(n=0.5, tau=1.0, eps=1.5, bounds=GridBounds.from_series(times, T))

    whole = k.log_likelihood_on_interval(times, mu, 0.0, T)
    first = k.log_likelihood_on_interval(times, mu, 0.0, mid)
    second = k.log_likelihood_on_interval(times, mu, mid, T)
    assert whole == pytest.approx(first + second, abs=1e-9)


def test_interval_over_the_whole_span_equals_the_full_likelihood():
    times = np.sort(np.random.default_rng(2).uniform(0, 400, 600))
    T, mu = 400.0, 0.5
    k = PowerLawKernel(n=0.5, tau=1.0, eps=1.5, bounds=GridBounds.from_series(times, T))
    assert k.log_likelihood_on_interval(times, mu, 0.0, T) == pytest.approx(
        k.log_likelihood(times, mu, T), abs=1e-9
    )


def test_interval_likelihood_conditions_on_pre_window_history():
    """Events before the window still excite the process inside it."""
    times = np.sort(np.random.default_rng(3).uniform(0, 400, 600))
    k = PowerLawKernel(n=0.5, tau=1.0, eps=1.5, bounds=GridBounds.from_series(times, 400.0))
    with_history = k.log_likelihood_on_interval(times, 0.5, 200.0, 400.0)
    tail_only = k.log_likelihood_on_interval(times[times > 200.0], 0.5, 200.0, 400.0)
    assert with_history != pytest.approx(tail_only, rel=1e-6)


def test_grid_bounds_use_a_quantile_not_the_minimum_gap():
    """REGRESSION. One near-simultaneous pair must not set the ceiling."""
    clean = np.arange(0.0, 1000.0, 1.0)
    contaminated = np.sort(np.concatenate([clean, [500.0 + 1e-9]]))
    a = GridBounds.from_series(clean, 1000.0)
    b = GridBounds.from_series(contaminated, 1000.0)
    assert b.beta_max < 10 * a.beta_max


def test_compensator_is_monotone_and_starts_at_the_baseline():
    times = np.array([1.0, 5.0, 20.0, 60.0])
    k = PowerLawKernel(n=0.5, tau=1.0, eps=1.5, bounds=WIDE)
    assert k.compensator(times, 0.1, 0.5) == pytest.approx(0.1 * 0.5)
    values = [k.compensator(times, 0.1, u) for u in (1.0, 10.0, 50.0, 100.0)]
    assert all(b > a for a, b in zip(values, values[1:]))


# -- simulation and recovery ------------------------------------------------


def test_simulator_is_reproducible():
    a = simulate_power_law(0.3, 0.5, 1.0, 1.5, 500.0, seed=7)
    b = simulate_power_law(0.3, 0.5, 1.0, 1.5, 500.0, seed=7)
    np.testing.assert_array_equal(a, b)


def test_simulator_rate_exceeds_the_baseline():
    """Self-excitation must raise the rate above mu; roughly mu/(1-n)."""
    mu, n, T = 0.3, 0.5, 4000.0
    ev = simulate_power_law(mu, n, 1.0, 1.5, T, seed=8)
    assert len(ev) / T > mu * 1.3
    assert np.all(np.diff(ev) > 0)


def test_simulator_rejects_supercritical():
    with pytest.raises(ValueError, match="explosive"):
        simulate_power_law(0.3, 1.2, 1.0, 1.5, 100.0, seed=9)


@pytest.mark.slow
def test_recovers_the_branching_ratio():
    """Study A, reduced. `n` is what the whole comparison rests on."""
    T = 4000.0
    ev = simulate_power_law(0.30, 0.50, 1.0, 1.5, T, seed=202)
    p = fit_power_law(ev, T=T, compute_std_errors=False)
    assert p.converged
    assert p.n == pytest.approx(0.50, abs=0.08)
    assert p.mu == pytest.approx(0.30, abs=0.05)


def test_fit_rejects_bad_input():
    with pytest.raises(ValueError, match="at least 2"):
        fit_power_law([1.0], T=10.0)
    with pytest.raises(ValueError, match="sorted"):
        fit_power_law([5.0, 1.0, 3.0], T=10.0)
    with pytest.raises(ValueError, match="ends before"):
        fit_power_law([1.0, 2.0, 30.0], T=10.0)


# -- reporting --------------------------------------------------------------


def _params(**kw) -> PowerLawParameters:
    base = dict(
        mu=0.1, n=0.5, tau=1.0, eps=1.5, time_unit="second",
        log_likelihood=-100.0, n_events=100, observation_window=10.0,
        converged=True, std_errors={},
    )
    return PowerLawParameters(**(base | kw))


def test_branching_ratio_and_stationarity():
    assert _params().branching_ratio == 0.5
    assert _params().is_stationary
    assert not _params(n=1.5).is_stationary


def test_eps_is_flagged_as_unidentified_without_a_tight_standard_error():
    """A recovery study gave se(eps) of 0.33-0.82 against eps ~ 1.5 - 22 to 54%
    relative - while se(n) was ~7%. Since eps -> 0 is the regime where n is
    argued to approach 1 spuriously, an eps without a tight interval cannot
    support any claim, and the result type says so rather than leaving it to
    the reader.
    """
    assert not _params(std_errors={}).eps_is_identified
    assert not _params(std_errors={"eps": 0.8}).eps_is_identified
    assert _params(std_errors={"eps": 0.1}).eps_is_identified
    assert "[eps NOT identified]" in _params(std_errors={"eps": 0.8}).describe()


def test_parameters_are_frozen():
    p = _params()
    with pytest.raises(Exception):
        p.n = 0.9
