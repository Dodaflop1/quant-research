"""Unit tests for the pure functions in the analysis scripts.

`src/` had 283 tests and `scripts/` had none, which is the wrong way round for a
repo whose conclusions come out of the scripts. Only value-in/value-out
functions are covered here — nothing that reads a file, calls an API, or shells
out. Those paths are exercised by running the scripts against real data.

Each function gets a normal case, a degenerate one, and at least one case that
would pass under a plausible but wrong simplification of the implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from detect_bucket_sum import best_bid, parse_ts
from field_size_scan import bucket_of
from market_calibration import (
    composition, is_auto_generated, outcome_of, parse_time, price_at_horizons,
    volume_of,
)
from power_law_studies import bimodality, percentiles
from probe_weather import (
    _paged, describe_buckets, describe_settlement, settlement_of,
)
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


# -- market_calibration -----------------------------------------------------
#
# Drafted by the local model, two corrected. Both of its failures were the same
# units error: it read `horizons_hours` as seconds. The cutoff is
# `close_ts - hours * 3600`, so a horizon of 1 reaches back an hour, and its
# fixtures spanned a few hundred seconds — the tests asserted prices the
# function cannot return.


def test_volume_of_reads_volume_fp_not_volume():
    """The live API returns `volume_fp` and omits `volume`. Reading the wrong
    key returned 0 for every market and silently emptied the sample."""
    assert volume_of({"volume_fp": "1234.00", "volume": 0}) == 1234.0
    assert volume_of({"volume": 500}) == 500.0
    assert volume_of({}) == 0.0
    assert volume_of({"volume_fp": "bad"}) == 0.0


def test_is_auto_generated_catches_mve_shards_and_provisional():
    """99% of the settled feed is machine-made parlay shards. Missing either
    flag readmits them and the sample becomes noise."""
    assert is_auto_generated({"mve_collection_ticker": "KXMVE-SHARD1"}) is True
    assert is_auto_generated({"is_provisional": True}) is True
    assert is_auto_generated({"mve_collection_ticker": None, "is_provisional": False}) is False
    assert is_auto_generated({}) is False


def test_price_at_horizons_excludes_trades_inside_the_horizon():
    """Lookahead: the h-hour price must not see anything after the cutoff.

    Using the last trade outright gives 0.90 here, which is the settled price
    and would make the market look clairvoyant.
    """
    close = 100_000.0
    trades = [(78_000.0, 55.0), (96_000.0, 65.0), (99_900.0, 90.0)]
    prices = price_at_horizons(trades, close, horizons_hours=[1, 6])
    assert prices["1"] == pytest.approx(0.65)     # cutoff 96_400
    assert prices["6"] == pytest.approx(0.55)     # cutoff 78_400


def test_a_horizon_older_than_the_first_trade_is_absent_not_zero():
    """A missing key means 'had not traded that far out'. A 0.0 would be scored
    as a confident NO and would wreck the calibration curve."""
    prices = price_at_horizons([(96_000.0, 65.0)], 100_000.0, horizons_hours=[1, 24])
    assert prices == {"1": pytest.approx(0.65)}


def test_price_at_horizons_handles_unsorted_input():
    """The tape does not arrive in order."""
    scrambled = [(96_000.0, 70.0), (90_000.0, 50.0), (95_000.0, 60.0)]
    a = price_at_horizons(sorted(scrambled), 100_000.0, [1])
    b = price_at_horizons(scrambled, 100_000.0, [1])
    assert a == b == {"1": pytest.approx(0.7)}


def test_price_at_horizons_returns_empty_for_no_trades():
    assert price_at_horizons([], 100.0, [1, 6]) == {}


def test_outcome_of_maps_yes_no_and_rejects_void():
    """A voided market has no outcome. Coercing it to 0 counts a refund as a
    NO and biases the base rate."""
    assert outcome_of({"result": "yes"}) == 1
    assert outcome_of({"result": "YES"}) == 1
    assert outcome_of({"result": "no"}) == 0
    assert outcome_of({"result": "voided"}) is None
    assert outcome_of({"result": ""}) is None
    assert outcome_of({}) is None
    assert outcome_of({"result": None}) is None


def test_composition_reports_the_concentration_that_invalidated_a_run():
    """The 210-market run was 79% one series and reported a verdict anyway."""
    records = ([{"series": "KXAFLGAME", "prices": {"1": 0.5}}] * 166
               + [{"series": "KXACTBLUETOP", "prices": {"1": 0.5}}] * 44)
    comp = composition(records)
    assert comp["n"] == 210
    assert comp["distinct_series"] == 2
    assert comp["top_series_share"] == pytest.approx(166 / 210, rel=0.01)
    assert comp["top"][0][0] == "KXAFLGAME"


def test_parse_time_rejects_junk_instead_of_raising():
    """One malformed timestamp mid-collection must not end a two-hour run."""
    assert parse_time("2026-08-24T12:00:00Z") is not None
    assert parse_time("") is None
    assert parse_time("not-a-date") is None
    assert parse_time(12345) is None
    assert parse_time(None) is None
    assert parse_time("2026-13-45T99:99:99Z") is None


def test_bimodality_without_the_mixture_fit_is_indeterminate_not_negative():
    """REGRESSION. `scikit-learn` is a declared dependency, but the mixture fit
    is wrapped in a try/except. When it did not run, the old code fell through
    to `split = False` and returned "not bimodal" — reporting a stale virtualenv
    as evidence about the data. On a clean split the two answers are opposite.
    """
    import builtins

    values = [0.20 + 0.01 * i for i in range(8)] + [0.86 + 0.01 * i for i in range(8)]
    real_import = builtins.__import__

    def no_sklearn(name, *args, **kwargs):
        if name.startswith("sklearn"):
            raise ImportError("No module named 'sklearn'")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = no_sklearn
    try:
        result = bimodality(values)
    finally:
        builtins.__import__ = real_import

    assert result["delta_bic"] is None
    assert result["verdict"] != "not bimodal"
    assert "indeterminate" in result["verdict"]
    assert result["gap_ratio"] > 3 / (len(values) - 1)   # the split is still visible


# -- probe_weather ----------------------------------------------------------
#
# The probe's job is to report a payload shape truthfully, including when the
# shape is not what the model expects. Both helpers are pure.


def test_describe_buckets_counts_the_open_ends():
    """A temperature family should have exactly one bucket with no floor and
    one with no cap. Any other count means the payload marks open ends
    differently — which the model must know before it builds a partition."""
    event = {"markets": [
        {"floor_strike": None, "cap_strike": 69, "ticker": "A", "subtitle": "69 or below"},
        {"floor_strike": 70, "cap_strike": 71, "ticker": "B", "subtitle": "70 to 71"},
        {"floor_strike": 86, "cap_strike": None, "ticker": "C", "subtitle": "86 or above"},
    ]}
    out = describe_buckets(event)
    assert out["n_markets"] == 3
    assert out["rows_missing_floor"] == 1
    assert out["rows_missing_cap"] == 1
    assert out["field_population"]["floor_strike"] == 2
    assert "ticker" in out["all_market_fields"]


def test_describe_buckets_survives_a_payload_with_none_of_the_expected_fields():
    """The probe exists precisely for the case where the fields are named
    something else. It must report that, not raise."""
    out = describe_buckets({"markets": [{"unexpected": 1}, {"unexpected": 2}]})
    assert out["n_markets"] == 2
    assert out["rows_missing_floor"] == 2
    assert all(v == 0 for v in out["field_population"].values())
    assert out["all_market_fields"] == ["unexpected"]


def test_describe_buckets_handles_an_event_with_no_markets():
    out = describe_buckets({})
    assert out["n_markets"] == 0 and out["rows"] == []


def test_describe_settlement_finds_the_station_and_the_rounding():
    """The half-degree bucket boundaries in the model are only correct if the
    settled value is a whole degree from a named station. This is the check
    that the assumption is the exchange's and not ours."""
    out = describe_settlement({
        "rules_primary": ("Settles to the maximum temperature recorded at KNYC "
                          "rounded to the nearest whole degree Fahrenheit."),
        "volume": 1234,
    })
    assert out["mentions_station_id"] == ["KNYC"]
    assert out["mentions_nearest_whole"] is True
    assert out["mentions_maximum"] is True
    assert out["mentions_degrees"] is True
    assert "volume" not in out["fields"]          # non-text fields are not scanned


def test_describe_settlement_reports_absence_rather_than_assuming():
    """No rules text means the assumption is unverified. Silence here must not
    read the same as confirmation."""
    out = describe_settlement({"ticker": "X"})
    assert out["fields"] == {}
    assert out["mentions_station_id"] == []
    assert out["mentions_nearest_whole"] is False
    assert out["mentions_maximum"] is False


class _FakeClient:
    """Minimal stand-in: hands back canned pages and counts the requests."""

    def __init__(self, pages):
        self._pages = pages
        self.calls = []

    def get(self, path, params):
        self.calls.append(params.get("cursor"))
        page = self._pages[len(self.calls) - 1]

        class _R:
            payload = page
        return _R()


def test_paged_reports_truncation_instead_of_looking_complete():
    """REGRESSION for the failure that made this probe report nothing.

    The first version stopped at its page cap and printed only how many records
    it had seen, so "no temperature series exist" and "the scan ran out of
    pages" were indistinguishable. They are opposite conclusions.
    """
    pages = [{"series": [1, 2], "cursor": "c1"},
             {"series": [3, 4], "cursor": "c2"},
             {"series": [5, 6], "cursor": "c3"}]
    rows, truncated = _paged(_FakeClient(pages), "/series", {}, "series", max_pages=2)
    assert rows == [1, 2, 3, 4]
    assert truncated is True


def test_paged_says_it_finished_when_the_cursor_runs_out():
    pages = [{"series": [1], "cursor": "c1"}, {"series": [2], "cursor": None}]
    rows, truncated = _paged(_FakeClient(pages), "/series", {}, "series", max_pages=10)
    assert rows == [1, 2]
    assert truncated is False


def test_paged_passes_the_cursor_forward():
    """Not passing it re-requests page one until the cap, which reads as a
    complete scan of a very repetitive universe."""
    pages = [{"series": [1], "cursor": "c1"}, {"series": [2], "cursor": None}]
    client = _FakeClient(pages)
    _paged(client, "/series", {"limit": 200}, "series", max_pages=10)
    assert client.calls == [None, "c1"]


def test_paged_handles_a_missing_key_without_inventing_rows():
    rows, truncated = _paged(_FakeClient([{"cursor": None}]), "/series", {}, "series", 5)
    assert rows == [] and truncated is False


# -- market_calibration resume ----------------------------------------------
#
# The point of a resume is crash safety, so these test the crash, not the
# happy path.


def test_a_missing_cache_starts_empty():
    from market_calibration import load_cache
    assert load_cache(Path("nowhere/at/all.json")) == ([], set())


def test_a_corrupt_cache_is_fatal_rather_than_silently_discarded(tmp_path):
    """REGRESSION. Logging a warning and starting fresh would overwrite however
    many hours of collection are in that file — turning one interrupted write
    into total data loss. Deleting it is a decision for a person."""
    from market_calibration import CorruptCache, load_cache
    bad = tmp_path / "settled.json"
    bad.write_text('{"records": [{"ticker": "A-1"', encoding="utf-8")
    with pytest.raises(CorruptCache, match="not readable JSON"):
        load_cache(bad)


def test_attempted_includes_tickers_that_yielded_no_record(tmp_path):
    """The two sets differ, and that difference is the value of the resume. A
    market whose tape yields no price at any horizon produces no record but
    still cost the API calls. Rebuilding "attempted" from records alone
    re-fetches every one of them on every restart."""
    from market_calibration import load_cache, save_cache
    cache = tmp_path / "settled.json"
    save_cache(cache, "settled", 100.0,
               [{"ticker": "A-1", "prices": {"1": 0.5}}],
               {"A-1", "B-2-no-trades", "C-3-no-trades"})
    records, attempted = load_cache(cache)
    assert [r["ticker"] for r in records] == ["A-1"]
    assert attempted == {"A-1", "B-2-no-trades", "C-3-no-trades"}


def test_a_cache_written_by_an_older_run_still_loads(tmp_path):
    """Files already on disk have no `attempted_tickers` key. They must resume,
    not raise — the whole point is not to throw away existing collection."""
    from market_calibration import load_cache
    cache = tmp_path / "settled.json"
    cache.write_text(json.dumps({"records": [{"ticker": "A-1"}, {"ticker": "B-2"}]}),
                     encoding="utf-8")
    records, attempted = load_cache(cache)
    assert len(records) == 2
    assert attempted == {"A-1", "B-2"}


def test_the_write_is_atomic_so_an_interrupted_flush_cannot_truncate(tmp_path):
    """A plain write_text on a multi-megabyte file is not atomic. Flushing
    often would then make the crash window MORE dangerous, which is backwards.
    Simulated by failing mid-write and checking the previous file survived."""
    import market_calibration as mc
    cache = tmp_path / "settled.json"
    mc.save_cache(cache, "settled", 100.0, [{"ticker": "GOOD"}], {"GOOD"})

    real_replace = mc.os.replace
    mc.os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("crash mid-rename"))
    try:
        with pytest.raises(OSError):
            mc.save_cache(cache, "settled", 100.0, [{"ticker": "NEW"}], {"NEW"})
    finally:
        mc.os.replace = real_replace

    survived = json.loads(cache.read_text(encoding="utf-8"))
    assert [r["ticker"] for r in survived["records"]] == ["GOOD"]


def test_saved_records_round_trip_through_the_cache(tmp_path):
    from market_calibration import load_cache, save_cache
    cache = tmp_path / "nested" / "settled.json"
    rows = [{"ticker": f"T-{i}", "outcome": i % 2, "prices": {"1": 0.5}} for i in range(3)]
    save_cache(cache, "settled", 100.0, rows, {r["ticker"] for r in rows})
    back, attempted = load_cache(cache)
    assert back == rows
    assert attempted == {"T-0", "T-1", "T-2"}
    assert not cache.with_suffix(cache.suffix + ".tmp").exists()   # temp is renamed away


def test_settlement_of_separates_the_free_source_from_the_proprietary_one():
    """The decisive fact about the whole temperature domain. Most daily
    temperature series settle on The Weather Company, whose history is not
    public; only the NWS-settled ones can be back-fitted from free data."""
    nws = settlement_of({"settlement_sources": [
        {"name": "NWS Climatological Report",
         "url": "https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC"}]})
    assert nws["settles_on"] == "National Weather Service"
    assert nws["cli_locations"] == ["NYC"]      # the id /products wants
    assert nws["cli_offices"] == ["OKX"]        # NOT the id /products wants

    twc = settlement_of({"settlement_sources": [
        {"name": "The Weather Company", "url": "https://weather.com/kalshi"}]})
    assert twc["settles_on"] == "The Weather Company (proprietary)"
    assert twc["cli_locations"] == []


def test_settlement_of_reports_an_absent_source_as_absent():
    """`SNOW` and `RAINMIA` carry no settlement_sources at all. That must not
    read as 'settles on the NWS'."""
    out = settlement_of({})
    assert out["settles_on"] == "none listed"
    assert out["cli_locations"] == [] and out["urls"] == []


def test_settlement_of_keeps_every_cli_location_in_a_multi_source_series():
    """Hurricane series list several offices. Taking only the first would
    silently drop locations the contract actually names."""
    out = settlement_of({"settlement_sources": [
        {"name": "National Weather Service",
         "url": "https://forecast.weather.gov/product.php?site=MHX&product=CLI&issuedby=HSE"},
        {"name": "National Weather Service",
         "url": "https://forecast.weather.gov/product.php?site=ILM&product=CLI&issuedby=CRE"},
    ]})
    assert out["cli_locations"] == ["CRE", "HSE"]
