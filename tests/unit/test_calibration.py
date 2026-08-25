"""Tests for the calibration metrics.

The hard part of testing a scoring rule is that almost any implementation
returns a plausible-looking number. These check the properties that pin the
implementation down: known closed forms, the decomposition identity, and the
cases where a wrong-but-reasonable implementation gives the opposite answer.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from quant.common.statistics.calibration import (
    brier_decomposition,
    brier_score,
    log_loss,
    reliability_curve,
    skill_score,
    spiegelhalter_z,
    wilson_interval,
)

RNG = np.random.default_rng(20260825)


# -- input validation -------------------------------------------------------


@pytest.mark.parametrize("probs,outs,match", [
    ([0.5, 0.5], [1], "shape"),
    ([], [], "no forecasts"),
    ([1.5, 0.5], [1, 0], r"\[0, 1\]"),
    ([0.5, float("nan")], [1, 0], "non-finite"),
    ([0.5, 0.5], [1, 2], "0 or 1"),
])
def test_bad_input_is_refused_not_scored(probs, outs, match):
    with pytest.raises(ValueError, match=match):
        brier_score(probs, outs)


def test_cent_prices_are_caught():
    """Kalshi prices are in cents. Passing them unconverted is the likeliest
    mistake anyone will make with this module, and it must not score."""
    with pytest.raises(ValueError, match="cents"):
        brier_score([65.0, 30.0], [1, 0])


# -- Brier ------------------------------------------------------------------


def test_brier_matches_the_closed_form_on_known_cases():
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0            # perfect
    assert brier_score([0.0, 1.0], [1, 0]) == 1.0            # maximally wrong
    assert brier_score([0.5, 0.5], [1, 0]) == 0.25           # coin flip


def test_always_predicting_the_base_rate_scores_the_uncertainty():
    """The reference every skill score is measured against."""
    y = np.array([1] * 30 + [0] * 70)
    base = y.mean()
    assert brier_score(np.full(100, base), y) == pytest.approx(base * (1 - base))


# -- log loss ---------------------------------------------------------------

def test_log_loss_matches_the_closed_form():
    assert log_loss([0.5, 0.5], [1, 0]) == pytest.approx(math.log(2))


def test_log_loss_punishes_a_confident_miss_far_harder_than_brier():
    """The property that makes it the right rule when the score feeds sizing.

    Brier is bounded, so a certain miss costs at most 1. Log loss is not.
    """
    cautious = ([0.6, 0.4], [0, 1])
    reckless = ([0.999, 0.001], [0, 1])
    brier_ratio = brier_score(*reckless) / brier_score(*cautious)
    loss_ratio = log_loss(*reckless) / log_loss(*cautious)
    assert brier_ratio < 3                      # measured 2.77 - Brier is bounded
    assert loss_ratio > 7                       # measured 7.54
    assert loss_ratio > 2.5 * brier_ratio       # the scale-free version of the claim


def test_a_certain_miss_is_large_but_finite():
    """Clipping, so one bad forecast degrades the score rather than destroying it."""
    value = log_loss([1.0], [0])
    assert np.isfinite(value) and value > 30


# -- the decomposition ------------------------------------------------------


def test_decomposition_is_exact_when_forecasts_are_discrete():
    """REGRESSION for the identity itself.

    `BS = REL - RES + UNC` holds exactly only when every forecast in a bin is
    identical. Three distinct values, each in its own bin, is that case — so the
    residual must vanish. If it does not, the binning or the weighting is wrong.
    """
    p = np.repeat([0.2, 0.5, 0.8], 200)
    y = (RNG.random(600) < p).astype(int)
    d = brier_decomposition(p, y, bins=[0.0, 0.35, 0.65, 1.0])
    assert d.residual == pytest.approx(0.0, abs=1e-12)
    assert d.n_bins_used == 3


def test_continuous_forecasts_leave_a_residual_and_it_is_reported():
    """The honest part: with continuous forecasts the three terms do NOT add up,
    and an implementation that silently reports zero is hiding the binning error.
    """
    p = RNG.uniform(0.05, 0.95, 4000)
    y = (RNG.random(4000) < p).astype(int)
    coarse = brier_decomposition(p, y, bins=4)
    fine = brier_decomposition(p, y, bins=50)
    assert abs(coarse.residual) > abs(fine.residual)
    assert abs(fine.residual) < 0.005


def test_a_calibrated_forecaster_hits_the_closed_form_exactly():
    """For calibrated forecasts drawn uniformly, the answer is known in advance.

    ``BS = E[p(1-p)] = 1/6`` and ``UNC = 0.25``, so the skill against
    climatology is exactly ``1 - (1/6)/(1/4) = 1/3``. Asserting the closed form
    beats asserting a loose bound: a bound of "> 0.5" passes for a broken
    implementation and fails for a correct one, which is what it did here.
    """
    p = RNG.uniform(0, 1, 20_000)
    y = (RNG.random(20_000) < p).astype(int)
    d = brier_decomposition(p, y, bins=20)
    assert d.brier == pytest.approx(1 / 6, abs=0.01)
    assert d.reliability < 0.002
    assert d.skill_vs_climatology == pytest.approx(1 / 3, abs=0.03)


def test_the_base_rate_parrot_is_calibrated_and_useless():
    """The case a single Brier score cannot distinguish, and the reason the
    decomposition exists. Perfect reliability, zero resolution, no skill."""
    y = np.array([1] * 300 + [0] * 700)
    RNG.shuffle(y)
    d = brier_decomposition(np.full(1000, 0.3), y, bins=10)
    assert d.reliability == pytest.approx(0.0, abs=1e-3)
    assert d.resolution == pytest.approx(0.0, abs=1e-9)
    assert d.skill_vs_climatology == pytest.approx(0.0, abs=1e-2)


def test_a_miscalibrated_but_informative_forecaster_shows_both_terms():
    """Squashed toward the middle: still ranks outcomes, systematically wrong.

    Reliability and resolution must BOTH be non-trivial. An implementation that
    conflates them would put everything in one term.
    """
    truth = RNG.uniform(0, 1, 8000)
    y = (RNG.random(8000) < truth).astype(int)
    squashed = 0.25 + 0.5 * truth
    d = brier_decomposition(squashed, y, bins=20)
    assert d.reliability > 0.01
    assert d.resolution > 0.02


def test_uncertainty_depends_only_on_the_outcomes():
    y = (RNG.random(500) < 0.4).astype(int)
    a = brier_decomposition(RNG.uniform(0, 1, 500), y, bins=10)
    b = brier_decomposition(np.full(500, 0.9), y, bins=10)
    assert a.uncertainty == pytest.approx(b.uncertainty)


# -- reliability curve ------------------------------------------------------


def test_reliability_bins_track_the_diagonal_when_calibrated():
    """A 95% interval is *supposed* to exclude the truth about 5% of the time.

    Requiring zero significant bins out of ten is therefore wrong, and fails on
    roughly 40% of seeds - which is how this test caught itself rather than the
    code. Measured over 20 seeds: mean 0.40 significant bins, max 1.
    """
    p = RNG.uniform(0, 1, 20_000)
    y = (RNG.random(20_000) < p).astype(int)
    curve = reliability_curve(p, y, bins=10, min_count=50)
    assert all(abs(b.gap) < 0.05 for b in curve)
    assert sum(b.is_significant for b in curve) <= 2


def test_a_thin_bin_is_not_evidence():
    """Four outcomes in a bin can sit far off the diagonal by chance. The
    interval must be wide enough to say so, and `min_count` must drop it."""
    p = np.array([0.5] * 4 + [0.1] * 400)
    y = np.array([1, 1, 1, 1] + [0] * 400)
    assert all(b.n >= 50 for b in reliability_curve(p, y, bins=10, min_count=50))
    thin = [b for b in reliability_curve(p, y, bins=10) if b.n == 4][0]
    assert thin.ci_high - thin.ci_low > 0.4


def test_the_top_bin_includes_a_forecast_of_exactly_one():
    """Off-by-one at the closed edge would silently drop every certain forecast."""
    curve = reliability_curve([1.0, 1.0, 0.05], [1, 1, 0], bins=10)
    assert sum(b.n for b in curve) == 3
    assert any(b.upper == 1.0 and b.n == 2 for b in curve)


# -- Wilson -----------------------------------------------------------------


def test_wilson_stays_inside_the_unit_interval_at_the_extremes():
    """Where the normal approximation goes negative."""
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0 and 0.0 < hi < 1.0
    lo, hi = wilson_interval(10, 10)
    assert hi == 1.0 and 0.0 < lo < 1.0


def test_wilson_narrows_with_sample_size():
    wide = wilson_interval(5, 10)
    narrow = wilson_interval(500, 1000)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0]) / 5


def test_wilson_of_nothing_is_nan():
    assert all(math.isnan(v) for v in wilson_interval(0, 0))


# -- Spiegelhalter ----------------------------------------------------------


def test_z_does_not_reject_a_calibrated_forecaster():
    p = RNG.uniform(0.05, 0.95, 5000)
    y = (RNG.random(5000) < p).astype(int)
    z, pval = spiegelhalter_z(p, y)
    assert abs(z) < 3.0
    assert pval > 0.003


def test_z_rejects_a_systematically_overconfident_forecaster():
    """Forecasts pushed toward the extremes: outcomes come in nearer the middle."""
    truth = RNG.uniform(0.05, 0.95, 5000)
    y = (RNG.random(5000) < truth).astype(int)
    overconfident = np.clip((truth - 0.5) * 1.8 + 0.5, 0.01, 0.99)
    z, pval = spiegelhalter_z(overconfident, y)
    assert abs(z) > 3.0
    assert pval < 0.01


def test_z_sign_says_which_direction_the_miscalibration_runs():
    """Positive z means outcomes exceeded forecasts — underconfident."""
    p = np.full(2000, 0.3)
    y = (RNG.random(2000) < 0.5).astype(int)
    z, _ = spiegelhalter_z(p, y)
    assert z > 0


def test_z_has_no_leverage_when_every_forecast_is_one_half():
    """The (1 - 2p) weight vanishes. Returning nan is correct; returning 0 would
    read as 'perfectly calibrated'."""
    z, pval = spiegelhalter_z(np.full(100, 0.5), (RNG.random(100) < 0.9).astype(int))
    assert math.isnan(z) and math.isnan(pval)


# -- skill ------------------------------------------------------------------


def test_skill_score_signs():
    assert skill_score(0.10, 0.25) == pytest.approx(0.6)
    assert skill_score(0.25, 0.25) == 0.0
    assert skill_score(0.40, 0.25) < 0


def test_skill_against_a_zero_reference_is_refused():
    with pytest.raises(ValueError, match="no skill scale"):
        skill_score(0.1, 0.0)
