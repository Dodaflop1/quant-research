"""Tests for the temperature fair-value model.

Three things here can be wrong while every number still looks reasonable: the
half-degree rounding boundary, the partition check, and the refusal to quote an
uncalibrated lead time. Each gets a test that fails for the plausible wrong
implementation rather than only for an absurd one.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from quant.kalshi.models.temperature import (
    LEAD_BUCKET_EDGES,
    Bucket,
    ErrorModel,
    ErrorSample,
    UncalibratedModel,
    bucket_probabilities,
    buckets_from_edges,
    parse_lead_hours,
)

RNG = np.random.default_rng(20260825)


def samples(n=400, bias=0.0, scale=3.0, lead=24.0, rng=RNG, forecast=75.0):
    errors = rng.normal(bias, scale, n)
    return [ErrorSample(lead_hours=lead, forecast_f=forecast, observed_f=forecast + e)
            for e in errors]


# -- the half-degree boundary ------------------------------------------------


def test_a_bucket_covers_the_half_degrees_around_its_integers():
    """THE detail. `72 to 73` settles on the reported integer, so it is the
    event round(T) in {72, 73}, which is T in [71.5, 73.5) — two degrees wide,
    not one. The plausible wrong version, [72, 73], is half as wide and biases
    every bucket in the family the same way."""
    lo, hi = Bucket(72, 73).continuous_bounds
    assert (lo, hi) == (71.5, 73.5)
    assert hi - lo == 2.0


def test_the_boundary_is_half_open_at_the_top():
    """73.5 rounds to 74 and belongs to the next bucket. An inclusive upper
    bound would put it in both, and the family would sum to more than one."""
    b = Bucket(72, 73)
    assert b.contains_observation(71.5) is True
    assert b.contains_observation(73.4999) is True
    assert b.contains_observation(73.5) is False
    assert b.contains_observation(71.4999) is False


def test_open_buckets_run_to_infinity():
    assert Bucket(None, 69).continuous_bounds == (-math.inf, 69.5)
    assert Bucket(86, None).continuous_bounds == (85.5, math.inf)
    with pytest.raises(ValueError, match="both ends"):
        Bucket(None, None)
    with pytest.raises(ValueError, match="empty bucket"):
        Bucket(75, 70)


# -- families ----------------------------------------------------------------


def test_buckets_from_edges_tiles_the_line_with_no_gap():
    buckets = buckets_from_edges([69, 71, 73])
    assert [(b.low, b.high) for b in buckets] == [
        (None, 69), (70, 71), (72, 73), (74, None)
    ]
    # Every integer in a wide span lands in exactly one bucket.
    for t in range(50, 100):
        assert sum(b.contains_observation(t) for b in buckets) == 1


def test_a_gap_in_the_family_is_refused_not_normalised_away():
    """A missing degree makes the raw probabilities sum to less than one.
    Normalising that away inflates every bucket by the same factor, which reads
    as an edge in every contract at once — the most seductive possible bug."""
    model = ErrorModel.fit(samples())
    holed = [Bucket(None, 69), Bucket(70, 71), Bucket(73, 74), Bucket(75, None)]
    with pytest.raises(ValueError, match="gap"):
        bucket_probabilities(holed, model, 72.0, 24.0)


def test_an_overlap_is_refused():
    model = ErrorModel.fit(samples())
    doubled = [Bucket(None, 71), Bucket(70, 73), Bucket(74, None)]
    with pytest.raises(ValueError, match="overlap"):
        bucket_probabilities(doubled, model, 72.0, 24.0)


def test_a_family_closed_at_the_end_is_refused():
    """Temperature has no upper bound. A family without an open top bucket
    cannot be a partition, whatever the exchange lists."""
    model = ErrorModel.fit(samples())
    with pytest.raises(ValueError, match="open at both ends"):
        bucket_probabilities([Bucket(None, 71), Bucket(72, 90)], model, 75.0, 24.0)


def test_family_probabilities_sum_to_one():
    model = ErrorModel.fit(samples())
    buckets = buckets_from_edges([69, 71, 73, 75, 77, 79])
    probs = bucket_probabilities(buckets, model, 74.0, 24.0)
    assert sum(probs) == pytest.approx(1.0)
    assert all(p >= 0 for p in probs)


def test_the_bucket_containing_the_forecast_is_the_most_likely():
    model = ErrorModel.fit(samples(bias=0.0, scale=3.0))
    buckets = buckets_from_edges([69, 71, 73, 75, 77, 79])
    probs = bucket_probabilities(buckets, model, 74.5, 24.0)
    winner = buckets[int(np.argmax(probs))]
    assert winner.contains_observation(74)


# -- fitting -----------------------------------------------------------------


def test_fit_recovers_a_known_bias_and_scale():
    model = ErrorModel.fit(samples(n=4000, bias=1.5, scale=2.5))
    got = model.summary(24.0)
    assert got["bias_f"] == pytest.approx(1.5, abs=0.15)
    assert got["scale_f"] == pytest.approx(2.5, abs=0.15)
    assert got["n"] == 4000


def test_a_warm_bias_moves_probability_the_right_way():
    """If the forecast runs 2F cold, the bucket above the forecast should be
    favoured over the one containing it. A sign error here is invisible in any
    aggregate and wrong on every trade."""
    cold = ErrorModel.fit(samples(n=4000, bias=2.0, scale=2.0))
    unbiased = ErrorModel.fit(samples(n=4000, bias=0.0, scale=2.0))
    above = Bucket(76, 77)
    assert cold.probability(above, 74.0, 24.0) > unbiased.probability(above, 74.0, 24.0)


def test_too_few_pairs_refuses_rather_than_widening():
    """The failure mode this project keeps paying for is a plausible number
    from an unmeasured quantity. Twenty pairs is not a spread."""
    with pytest.raises(UncalibratedModel, match="reached 30 samples"):
        ErrorModel.fit(samples(n=20))


def test_an_uncalibrated_lead_time_raises_and_says_what_is_calibrated():
    model = ErrorModel.fit(samples(n=200, lead=24.0))
    with pytest.raises(UncalibratedModel) as exc:
        model.probability(Bucket(72, 73), 74.0, 120.0)
    assert "24-48h" in str(exc.value)          # the message names what it has


def test_lead_buckets_are_fitted_separately_and_widen_with_lead():
    """One pooled error distribution would be too narrow at a day and too wide
    at five. The fit must keep them apart."""
    rows = samples(n=500, scale=2.0, lead=18.0) + samples(n=500, scale=5.0, lead=100.0)
    model = ErrorModel.fit(rows)
    near = model.summary(18.0)["scale_f"]
    far = model.summary(100.0)["scale_f"]
    assert near == pytest.approx(2.0, abs=0.3)
    assert far == pytest.approx(5.0, abs=0.6)
    assert far > 2 * near


def test_a_lead_beyond_the_last_edge_uses_the_widest_bucket():
    """A ten-day forecast is scored with the widest measured error, not
    dropped. Dropping it silently would thin the sample at exactly the horizons
    where the model is least sure."""
    last = len(LEAD_BUCKET_EDGES) - 2
    assert parse_lead_hours(LEAD_BUCKET_EDGES, 1_000.0) == last
    assert parse_lead_hours(LEAD_BUCKET_EDGES, 0.0) == 0
    with pytest.raises(ValueError, match="non-negative"):
        parse_lead_hours(LEAD_BUCKET_EDGES, -1.0)


def test_identical_forecasts_do_not_produce_a_zero_scale_model():
    """A zero spread would price every bucket at 0 or 1 and invite an unbounded
    position. In practice it means duplicated rows, not solved weather."""
    rows = [ErrorSample(24.0, 75.0, 75.0) for _ in range(100)]
    with pytest.raises(UncalibratedModel):
        ErrorModel.fit(rows)


# -- the empirical family ----------------------------------------------------


def test_the_empirical_family_follows_a_skewed_sample_the_gaussian_misses():
    """Forecast errors are not symmetric — a heat wave overshoots further than
    a cool day undershoots. Inside the observed range the ECDF should track the
    sample where the Gaussian does not."""
    errors = RNG.gumbel(0.0, 2.0, 3000)
    rows = [ErrorSample(24.0, 75.0, 75.0 + e) for e in errors]
    emp = ErrorModel.fit(rows, family="empirical")
    gauss = ErrorModel.fit(rows, family="gaussian")
    truth = float((errors <= 4.0).mean())
    assert abs(emp.cdf(4.0, 24.0) - truth) < abs(gauss.cdf(4.0, 24.0) - truth)
    assert emp.cdf(4.0, 24.0) == pytest.approx(truth, abs=0.01)


def test_the_empirical_tail_is_small_but_never_zero():
    """Beyond every residual ever observed the probability is small, not zero.
    A zero prices the contract at 0c and invites an unbounded short."""
    model = ErrorModel.fit(samples(n=500), family="empirical")
    far = model.probability(Bucket(140, None), 75.0, 24.0)
    assert 0.0 < far < 1e-3


def test_the_empirical_cdf_is_monotone_across_the_graft():
    model = ErrorModel.fit(samples(n=300), family="empirical")
    xs = np.linspace(-30, 30, 400)
    vals = [model.cdf(float(x), 24.0) for x in xs]
    assert all(b >= a - 1e-12 for a, b in zip(vals, vals[1:]))
    assert 0.0 <= vals[0] and vals[-1] <= 1.0


# -- standard error ----------------------------------------------------------


def test_the_standard_error_falls_like_one_over_root_n():
    """Four times the data should halve it. A stderr that ignores n would be
    flat, and a stderr that double counts would fall too fast."""
    small = ErrorModel.fit(samples(n=500, rng=np.random.default_rng(1)))
    large = ErrorModel.fit(samples(n=8000, rng=np.random.default_rng(2)))
    b = Bucket(74, 75)
    ratio = small.stderr_of_probability(b, 74.0, 24.0) / large.stderr_of_probability(b, 74.0, 24.0)
    assert ratio == pytest.approx(4.0, rel=0.25)      # sqrt(8000/500) = 4


def test_the_standard_error_is_smallest_in_the_tail_which_is_the_warning():
    """REGRESSION for the documented limitation, not for a bug.

    Parameter uncertainty is near zero for a far tail bucket, because moving
    the fitted mean barely changes an already-tiny probability. That is exactly
    where the Gaussian shape assumption is worst. If this ever stops holding,
    the docstring warning is wrong and should change with it.
    """
    model = ErrorModel.fit(samples(n=2000))
    centre = model.stderr_of_probability(Bucket(74, 75), 74.0, 24.0)
    tail = model.stderr_of_probability(Bucket(95, None), 74.0, 24.0)
    assert tail < centre / 100
    assert model.probability(Bucket(95, None), 74.0, 24.0) < 1e-6


# -- persistence -------------------------------------------------------------


def test_a_saved_model_scores_identically_when_reloaded(tmp_path):
    """A model refitted on every run is a model whose backtest cannot be
    reproduced."""
    model = ErrorModel.fit(samples(n=300), family="empirical", station="KNYC")
    path = tmp_path / "err.json"
    model.to_json(path)
    back = ErrorModel.from_json(path)
    assert back.station == "KNYC"
    for t in (70.0, 74.0, 79.0):
        b = Bucket(int(t), int(t) + 1)
        assert back.probability(b, 74.0, 24.0) == pytest.approx(
            model.probability(b, 74.0, 24.0)
        )
    assert json.loads(path.read_text())["family"] == "empirical"
