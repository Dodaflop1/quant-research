"""Unit tests for the pure functions in the analysis scripts.

`src/` had 283 tests and `scripts/` had none, which is the wrong way round for a
repo whose conclusions come out of the scripts. Only value-in/value-out
functions are covered here — nothing that reads a file, calls an API, or shells
out. Those paths are exercised by running the scripts against real data.

Each function gets a normal case, a degenerate one, and at least one case that
would pass under a plausible but wrong simplification of the implementation.
"""

from __future__ import annotations

import numpy as np
import pytest

from detect_bucket_sum import best_bid, parse_ts
from field_size_scan import bucket_of
from power_law_studies import bimodality, percentiles
from sensitivity_sweep import fmt_spread, spread
from verify_complementarity import ladder, quantiles, read_book
from window_stationarity import dispersion, halves_ratio


# -- window_stationarity ----------------------------------------------------


def test_dispersion_is_about_one_for_a_uniform_series():
    """Index of dispersion is 1 under Poisson, which is the whole point of it."""
    times = np.sort(np.random.default_rng(42).uniform(0, 100, 2000))
    assert 0.5 < dispersion(times, T=100.0, bins=20) < 2.0


def test_dispersion_of_an_empty_series_is_nan_not_zero():
    """Zero would read as 'perfectly regular'. It means 'no data'."""
    assert np.isnan(dispersion(np.array([]), T=100.0, bins=20))


def test_dispersion_sees_a_gap_that_a_variance_of_times_would_miss():
    """REGRESSION-ish. The statistic must respond to *where* events fall.

    A series split into two tight clumps has the same mean time as a uniform
    one. Binning is what distinguishes them, so this fails if the statistic is
    ever "simplified" to a spread of the timestamps.
    """
    rng = np.random.default_rng(123)
    clumped = np.sort(np.concatenate([rng.uniform(0, 5, 500), rng.uniform(95, 100, 500)]))
    uniform = np.sort(rng.uniform(0, 100, 1000))
    assert dispersion(clumped, 100.0, 20) > 10 * dispersion(uniform, 100.0, 20)


def test_halves_ratio_is_orientation_free():
    """Busier over quieter, whichever half is busier — never a signed ratio."""
    early = np.sort(np.random.default_rng(1).uniform(0, 40, 900))
    late = 100.0 - early[::-1]
    assert halves_ratio(early, 100.0) == pytest.approx(halves_ratio(late, 100.0), rel=0.05)
    assert halves_ratio(early, 100.0) > 1.0


def test_halves_ratio_never_divides_by_zero():
    """An empty half is the case that would crash a naive implementation."""
    assert halves_ratio(np.array([]), 100.0) == 1.0
    assert halves_ratio(np.array([1.0, 2.0, 3.0]), 100.0) >= 1.0


# -- power_law_studies ------------------------------------------------------


def test_bimodality_finds_a_real_split():
    values = [0.20 + 0.01 * i for i in range(8)] + [0.86 + 0.01 * i for i in range(8)]
    result = bimodality(values)
    assert result["verdict"] == "bimodal"
    assert result["delta_bic"] > 10.0
    assert len(result["component_means"]) == 2


def test_bimodality_rejects_a_wide_smear():
    """The test the whole function exists for.

    A wide unimodal sample has high variance and no second mode. An
    implementation that answered with a variance — as the first attempt at this
    study did — calls this bimodal.
    """
    values = list(np.random.default_rng(7).normal(0.5, 0.2, 60))
    assert bimodality(values)["verdict"] == "not bimodal"
    assert np.var(values) > 0.02  # genuinely wide, so variance would be fooled


def test_bimodality_refuses_to_judge_a_tiny_sample():
    assert bimodality([0.2, 0.9, 0.2])["verdict"] == "too few points"
    assert bimodality([])["count"] == 0


def test_percentiles_drops_none_but_keeps_zero():
    """0.0 is a legitimate branching ratio; `if v` would silently discard it."""
    out = percentiles([0.0, None, 1.0, 2.0])
    assert out["count"] == 3
    assert out["median"] == 1.0


# -- detect_bucket_sum ------------------------------------------------------


def test_parse_ts_round_trips_an_iso_timestamp():
    assert parse_ts("2026-08-24T12:00:00Z") == pytest.approx(1787572800.0, abs=1)


@pytest.mark.parametrize("bad", ["", "not-a-date", 12345, None, {}, "2026-13-45T99:99:99Z"])
def test_parse_ts_returns_none_rather_than_raising(bad):
    """A bad timestamp on one line must not abort a 189,000-line scan."""
    assert parse_ts(bad) is None


def test_best_bid_sums_size_at_the_touch():
    """Two resting orders at the same price are one level, not two."""
    payload = {"orderbook_fp": {"yes_dollars": [["0.60", "100"], ["0.60", "50"],
                                                ["0.55", "200"]]}}
    price, size = best_bid(payload)
    assert price == pytest.approx(60.0)
    assert size == pytest.approx(150.0)


def test_best_bid_ignores_zero_size_levels():
    """A level with no size is not a bid, and taking it would fabricate a touch."""
    payload = {"orderbook_fp": {"yes_dollars": [["0.90", "0"], ["0.55", "200"]]}}
    price, _ = best_bid(payload)
    assert price == pytest.approx(55.0)


@pytest.mark.parametrize("payload", [{}, {"orderbook_fp": {}},
                                     {"orderbook_fp": {"yes_dollars": []}},
                                     {"orderbook_fp": "not a dict"}])
def test_best_bid_returns_none_when_there_is_no_bid(payload):
    assert best_bid(payload) is None


# -- field_size_scan --------------------------------------------------------


@pytest.mark.parametrize("legs,expected", [(2, "2"), (3, "3-4"), (4, "3-4"), (9, "5-9"),
                                           (10, "10-19"), (49, "20-49"), (50, "50+"),
                                           (184, "50+")])
def test_bucket_of_covers_every_field_size(legs, expected):
    assert bucket_of(legs) == expected


# -- verify_complementarity -------------------------------------------------


def test_ladder_scales_dollars_to_cents():
    assert ladder([["0.60", "100"]], 100.0) == [(pytest.approx(60.0), 100.0)]


def test_ladder_skips_malformed_entries_without_dropping_good_ones():
    out = ladder([["0.60", "100"], ["bad", "data"], [], ["0.55", "200"]], 100.0)
    assert len(out) == 2


def test_read_book_does_not_double_scale_a_cents_payload():
    """The units bug that would put every price off by 100x."""
    yes, no, shape = read_book({"orderbook_fp": {"yes": [[60, 100]], "no": [[39, 50]]}})
    assert shape == "cents"
    assert yes == [(60.0, 100.0)]
    assert no == [(39.0, 50.0)]


def test_read_book_reports_an_empty_book_distinctly_from_a_broken_one():
    assert read_book({})[2] == "empty"
    assert read_book({"orderbook_fp": "nonsense"})[2] == "not-a-dict"


def test_quantiles_is_order_independent():
    a = quantiles([5, 1, 9, 3, 7], qs=(0.0, 0.5, 1.0))
    b = quantiles([1, 3, 5, 7, 9], qs=(0.0, 0.5, 1.0))
    assert a == b == {"p0": 1, "p50": 5, "p100": 9}


def test_quantiles_of_nothing_is_empty_not_zero():
    assert quantiles([]) == {}


# -- sensitivity_sweep ------------------------------------------------------


def test_spread_reports_a_band_not_just_a_centre():
    out = spread([float(i) for i in range(1, 11)])
    assert out["count"] == 10
    assert out["median"] == pytest.approx(5.5)
    assert out["p10"] < out["median"] < out["p90"]


def test_spread_of_nothing_is_none_not_nan():
    """None renders as a dash; NaN renders as 'nan' in a results table."""
    out = spread([])
    assert out["median"] is None and out["count"] == 0


def test_spread_does_not_let_one_nan_poison_a_cell():
    """A single non-finite value would otherwise make the whole median NaN,
    and a NaN in one grid cell reads as 'this configuration failed' when in
    fact one market failed."""
    out = spread([1.0, float("nan"), 3.0, 5.0])
    assert out["count"] == 3
    assert out["median"] == pytest.approx(3.0)


def test_fmt_spread_survives_a_block_missing_its_band():
    """Reporting must not crash on a partial result; the run has already cost
    an hour by the time formatting happens."""
    assert fmt_spread({}) == "–"
    assert fmt_spread({"median": None}) == "–"
    assert fmt_spread({"median": 0.5}) != ""
    assert "0.500" in fmt_spread({"median": 0.5, "p10": 0.45, "p90": 0.55})
