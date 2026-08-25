"""Tests for seasonality estimation, the time change, and held-out scoring.

The correction these test exists because of a measured failure: a
constant-baseline Hawkes fit reports a branching ratio around 0.79-0.95 on data
with *zero* self-excitation, purely by absorbing a varying rate. Verified end to
end below — seasonal Poisson goes from 0.95 naive to 0.06 corrected, while
genuine excitation of 0.5 survives the same correction at 0.477.
"""

from __future__ import annotations

import numpy as np
import pytest

from quant.diffusion.hawkes.baseline import (
    SeasonalProfile,
    _phase_exposure,
    deseasonalise,
)
from quant.diffusion.hawkes.model import (
    fit,
    log_likelihood,
    log_likelihood_on_interval,
    poisson_log_likelihood_on_interval,
)
from quant.diffusion.hawkes.simulation import simulate_cluster


def seasonal_poisson(rate, amp, period, T, seed):
    rng = np.random.default_rng(seed)
    ceiling = rate * (1 + amp)
    c = np.sort(rng.uniform(0, T, size=rng.poisson(ceiling * T)))
    lam = rate * (1 + amp * np.sin(2 * np.pi * c / period))
    return c[rng.uniform(0, ceiling, size=len(c)) <= lam]


# -- exposure ---------------------------------------------------------------


def test_exposure_is_uniform_over_whole_periods():
    exp = _phase_exposure(T=1000.0, period=100.0, n_bins=10)
    np.testing.assert_allclose(exp, 100.0)


def test_exposure_accounts_for_a_partial_period():
    """A window of 2.5 periods gives early bins one more visit than late ones.

    Assuming uniform exposure would read that purely calendrical asymmetry as a
    50% seasonal effect — inventing exactly the thing being corrected for.
    """
    exp = _phase_exposure(T=250.0, period=100.0, n_bins=10)
    assert exp[:5] == pytest.approx(30.0)
    assert exp[5:] == pytest.approx(20.0)
    assert exp.sum() == pytest.approx(250.0)


def test_exposure_always_sums_to_the_window():
    for T in (37.0, 100.0, 512.5, 999.9):
        assert _phase_exposure(T, 100.0, 7).sum() == pytest.approx(T)


# -- profile estimation -----------------------------------------------------


def test_recovers_the_shape_of_a_known_sinusoid():
    period = 1000.0
    times = seasonal_poisson(2.0, 0.8, period, 60_000.0, seed=1)
    prof = SeasonalProfile.estimate(times, 60_000.0, period=period, n_bins=20)

    assert not prof.is_flat
    assert prof.factors.mean() == pytest.approx(1.0, abs=1e-9)
    # sin peaks a quarter of the way through the period
    assert int(np.argmax(prof.factors)) == pytest.approx(5, abs=2)
    assert prof.peak_to_trough > 3.0


def test_declines_to_estimate_when_bins_would_be_noise():
    """200 events over 24 bins is 8 per bin; Poisson noise alone gives a
    peak-to-trough near 3, and 'correcting' for it fabricates structure."""
    times = np.sort(np.random.default_rng(2).uniform(0, 1000, size=200))
    prof = SeasonalProfile.estimate(times, 1000.0, period=100.0, n_bins=24)
    assert prof.is_flat
    assert "per bin" in prof.reason
    np.testing.assert_allclose(prof.factors, 1.0)


def test_a_flat_profile_is_reported_not_silently_applied():
    prof = SeasonalProfile.estimate(np.array([1.0, 2.0]), 10.0)
    assert prof.is_flat
    assert "NOT APPLIED" in prof.describe()


def test_homogeneous_poisson_gives_a_near_flat_profile():
    times = np.sort(np.random.default_rng(3).uniform(0, 60_000, size=30_000))
    prof = SeasonalProfile.estimate(times, 60_000.0, period=1000.0, n_bins=20)
    assert prof.peak_to_trough < 1.35


# -- the time change --------------------------------------------------------


def test_operational_time_is_strictly_increasing():
    times = seasonal_poisson(2.0, 0.9, 1000.0, 40_000.0, seed=4)
    ops = deseasonalise(times, 40_000.0, period=1000.0, n_bins=20)[0]
    assert np.all(np.diff(ops) > 0)


def test_a_flat_profile_leaves_times_unchanged():
    times = np.array([1.0, 5.0, 50.0])
    prof = SeasonalProfile(np.ones(10), 100.0, 3, is_flat=True)
    np.testing.assert_allclose(prof.to_operational_time(times), times)


def test_one_full_period_maps_to_its_own_length():
    """The profile has mean 1, so operational and wall time agree per period."""
    prof = SeasonalProfile(
        np.array([0.5, 1.5, 0.5, 1.5]), period=100.0, n_events=99, is_flat=False
    )
    assert prof.to_operational_time(np.array([100.0]))[0] == pytest.approx(100.0)
    assert prof.to_operational_time(np.array([300.0]))[0] == pytest.approx(300.0)


def test_time_change_stretches_busy_phases_and_compresses_quiet_ones():
    prof = SeasonalProfile(
        np.array([0.5, 1.5]), period=100.0, n_events=99, is_flat=False
    )
    # First half runs at 0.5, so 50 wall seconds is 25 operational seconds.
    assert prof.to_operational_time(np.array([50.0]))[0] == pytest.approx(25.0)
    # Second half runs at 1.5: 25 + 50*1.5 = 100.
    assert prof.to_operational_time(np.array([100.0]))[0] == pytest.approx(100.0)


# -- the property the whole approach rests on -------------------------------


def test_the_correction_removes_a_spurious_branching_ratio():
    """Seasonal Poisson: true n = 0. Naive reports ~0.95.

    This is the headline. Without the correction the fit lands near critical on
    data containing no self-excitation at all.
    """
    T, period = 20_000.0, 1000.0
    times = seasonal_poisson(1.0, 0.8, period, T, seed=9000)

    naive = fit(times, T=T, compute_std_errors=False)
    ops, ops_T, prof = deseasonalise(times, T, period=period, n_bins=20)
    corrected = fit(ops, T=ops_T, compute_std_errors=False)

    assert naive.branching_ratio > 0.7, "expected the naive fit to be inflated"
    assert corrected.branching_ratio < 0.19, (
        f"corrected n={corrected.branching_ratio:.3f} did not fall below the "
        "0.19 noise floor from the Poisson negative control"
    )
    assert prof.peak_to_trough > 3.0


def test_the_correction_does_not_destroy_genuine_excitation():
    """Real Hawkes, no seasonality, true n = 0.5. The correction must be
    specific to seasonality rather than shrinking everything it touches."""
    T = 6000.0
    times, _ = simulate_cluster(0.5, 0.8, 1.6, T, seed=400)

    naive = fit(times, T=T, compute_std_errors=False)
    ops, ops_T, _ = deseasonalise(times, T, period=1000.0, n_bins=20)
    corrected = fit(ops, T=ops_T, compute_std_errors=False)

    assert corrected.branching_ratio == pytest.approx(0.5, abs=0.1)
    assert abs(naive.branching_ratio - corrected.branching_ratio) < 0.05


# -- held-out scoring -------------------------------------------------------


def test_interval_likelihood_over_the_whole_span_matches_the_full_likelihood():
    times, _ = simulate_cluster(0.5, 0.8, 1.6, 3000.0, seed=11)
    T = 3000.0
    full = log_likelihood(times, 0.5, 0.8, 1.6, T)
    piece = log_likelihood_on_interval(times, 0.5, 0.8, 1.6, 0.0, T)
    assert piece == pytest.approx(full, rel=1e-9)


def test_interval_likelihood_is_additive_across_a_split():
    times, _ = simulate_cluster(0.5, 0.8, 1.6, 3000.0, seed=12)
    T, mid = 3000.0, 1800.0
    whole = log_likelihood_on_interval(times, 0.5, 0.8, 1.6, 0.0, T)
    a = log_likelihood_on_interval(times, 0.5, 0.8, 1.6, 0.0, mid)
    b = log_likelihood_on_interval(times, 0.5, 0.8, 1.6, mid, T)
    assert a + b == pytest.approx(whole, rel=1e-9)


def test_interval_likelihood_conditions_on_pre_window_history():
    """Dropping the history is a different model, and it must show.

    Events before the test window still excite the process inside it, so a
    held-out score that discards them is not scoring the model that was fitted.
    """
    times, _ = simulate_cluster(0.5, 0.8, 1.6, 3000.0, seed=13)
    mid = 1500.0
    with_history = log_likelihood_on_interval(times, 0.5, 0.8, 1.6, mid, 3000.0)
    tail = times[times > mid]
    without = log_likelihood_on_interval(tail, 0.5, 0.8, 1.6, mid, 3000.0)
    assert with_history != pytest.approx(without, rel=1e-6)


def test_hawkes_beats_poisson_out_of_sample_on_hawkes_data():
    T, split = 8000.0, 6000.0
    times, _ = simulate_cluster(0.5, 0.8, 1.6, T, seed=14)
    train = times[times <= split]
    p = fit(train, T=split, compute_std_errors=False)

    hawkes = log_likelihood_on_interval(times, p.mu, p.alpha, p.beta, split, T)
    poisson = poisson_log_likelihood_on_interval(
        times, len(train) / split, split, T
    )
    assert hawkes > poisson


def test_hawkes_barely_beats_poisson_on_poisson_data():
    """On data with no excitation the gain should be small, not large.

    A large held-out gain on independent arrivals would mean the comparison is
    not measuring what it claims to.
    """
    T, split, rate = 8000.0, 6000.0, 1.0
    rng = np.random.default_rng(15)
    times = np.sort(rng.uniform(0, T, size=rng.poisson(rate * T)))
    train = times[times <= split]
    p = fit(train, T=split, compute_std_errors=False)

    hawkes = log_likelihood_on_interval(times, p.mu, p.alpha, p.beta, split, T)
    poisson = poisson_log_likelihood_on_interval(
        times, len(train) / split, split, T
    )
    n_test = int(np.sum(times > split))
    assert abs(hawkes - poisson) / n_test < 0.05


def test_poisson_likelihood_matches_the_closed_form():
    times = np.array([1.0, 2.0, 3.0, 7.0, 9.0])
    got = poisson_log_likelihood_on_interval(times, 0.5, 0.0, 10.0)
    assert got == pytest.approx(5 * np.log(0.5) - 0.5 * 10.0)


def test_scoring_rejects_a_degenerate_window():
    times = np.array([1.0, 2.0, 3.0])
    assert log_likelihood_on_interval(times, 1.0, 0.5, 1.0, 5.0, 5.0) == -np.inf
    assert poisson_log_likelihood_on_interval(times, 1.0, 5.0, 5.0) == -np.inf
    assert poisson_log_likelihood_on_interval(times, 0.0, 0.0, 10.0) == -np.inf
