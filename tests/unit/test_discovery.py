"""Tests for market selection.

The behaviour under test is an asymmetry the exchange's metadata half-hides.
Shorting a basket needs only mutual exclusivity, which the API does flag.
Buying one needs collective exhaustiveness, which it does not flag, titles
cannot reveal, and a price snapshot cannot settle.

Fixtures use the live payload shape captured from a production response on
2026-08-23 — unit-suffixed, string-valued fields — because reading the bare
names in the API documentation found zero quotes on all 4,987 mutually
exclusive families.
"""

import pytest

from quant.ingest.discovery import classify, discover, tickers_of


def mkt(ticker, ask=None, bid=None, volume="1000.00", ask_size="500.00", bid_size="500.00"):
    """A market shaped like the real thing."""
    m = {
        "ticker": ticker,
        "volume_fp": volume,
        "liquidity_dollars": "0.0000",
        "open_interest_fp": "40101.16",
        "yes_ask_size_fp": ask_size,
        "yes_bid_size_fp": bid_size,
        "status": "active",
    }
    m["yes_ask_dollars"] = f"{ask / 100:.4f}" if ask is not None else "0.0000"
    m["yes_bid_dollars"] = f"{bid / 100:.4f}" if bid is not None else "0.0000"
    return m


def legacy_mkt(ticker, ask=None, bid=None):
    """The documented-but-absent field spelling, kept parseable as a fallback."""
    return {"ticker": ticker, "yes_ask": ask, "yes_bid": bid, "volume": 1000}


def event(ticker="E1", me=True, series="KXTEST", title="Test", markets=None):
    return {
        "event_ticker": ticker,
        "series_ticker": series,
        "title": title,
        "mutually_exclusive": me,
        "markets": markets or [],
    }


class _Client:
    def __init__(self, events):
        self._events = events

    def iter_events(self, status="open", with_nested_markets=True):
        return iter(self._events)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_spread_straddling_hundred_is_the_no_arbitrage_state():
    """What a functioning market looks like: bids <= 100c <= asks.

    Every family in the live 2026-08-23 run was here.
    """
    markets = [mkt("A", ask=61, bid=59), mkt("B", ask=41, bid=39)]
    fam = classify(event(markets=markets), markets)
    assert fam.status == "sandwich"
    assert fam.ask_sum_cents == pytest.approx(102)
    assert fam.bid_sum_cents == pytest.approx(98)


def test_bids_summing_above_hundred_is_a_short_arbitrage():
    """Selling every leg collects more than the 100c that can ever be owed."""
    markets = [mkt("A", ask=70, bid=68), mkt("B", ask=40, bid=38)]
    fam = classify(event(markets=markets), markets)
    assert fam.status == "short_arb"
    assert fam.bid_sum_cents == pytest.approx(106)


def test_short_direction_needs_only_mutual_exclusivity():
    """A leaky family is not an obstacle to the short trade — it improves it.

    If an unlisted outcome wins, every short expires worthless and the whole
    premium is kept. So exhaustiveness is not required, and the exchange's own
    flag is enough to identify this direction.
    """
    markets = [mkt(f"M{i}", ask=p + 2, bid=p) for i, p in enumerate((40, 35, 32))]
    fam = classify(event(title="Who will the next Pope be?", markets=markets), markets)
    assert fam.bid_sum_cents == pytest.approx(107)
    assert fam.status == "short_arb"


def test_asks_summing_below_hundred_is_ambiguous_not_an_arb():
    """Either an arbitrage or a family leaking probability. A snapshot cannot say."""
    markets = [mkt(f"M{i}", ask=p, bid=p - 2) for i, p in enumerate((20, 15, 10, 7))]
    fam = classify(event(title="Who will the next Pope be?", markets=markets), markets)
    assert fam.status == "long_watch"
    assert "arb or a leaky family" in fam.reason


def test_large_field_overround_is_not_an_error():
    """Regression for the band heuristic that rejected 293 live families at 243c.

    The minimum tick props up every longshot in a big field, so the summed ask
    runs far above 100c by construction. That is the overround, not bad data,
    and the bids are where the opportunity would be.
    """
    markets = [mkt(f"R{i}", ask=2, bid=1) for i in range(184)]
    fam = classify(event(title="Vuelta stage winner", markets=markets), markets)
    assert fam.ask_sum_cents == pytest.approx(368)
    assert fam.status == "short_arb"  # bids sum to 184c
    assert "implausible" not in fam.reason


def test_not_flagged_mutually_exclusive_is_rejected():
    """Without it, more than one leg can pay and the 100c bound is gone."""
    markets = [mkt("A", ask=50, bid=48), mkt("B", ask=50, bid=48)]
    fam = classify(event(me=False, markets=markets), markets)
    assert fam.status == "rejected"
    assert "mutually exclusive" in fam.reason


def test_single_market_event_rejected():
    markets = [mkt("A", ask=100, bid=98)]
    assert classify(event(markets=markets), markets).reason == "single market"


def test_unquoted_family_rejected():
    markets = [mkt("A"), mkt("B")]
    fam = classify(event(markets=markets), markets)
    assert fam.status == "rejected"
    assert fam.reason == "no quotes"
    assert fam.ask_sum_cents is None


def test_mostly_unquoted_family_rejected():
    """A sum from two of ten legs means nothing."""
    markets = [mkt("A", ask=50, bid=48), mkt("B", ask=50, bid=48)]
    markets += [mkt(f"X{i}") for i in range(8)]
    fam = classify(event(markets=markets), markets)
    assert fam.status == "rejected"
    assert "legs quoted" in fam.reason


def test_collectable_covers_everything_that_quotes():
    """The no-arbitrage majority is still worth a time series.

    A dislocation, if one happens, happens to a family that looked ordinary.
    """
    ok = [mkt("A", ask=51, bid=49), mkt("B", ask=51, bid=49)]
    assert classify(event(markets=ok), ok).collectable
    bad = [mkt("A"), mkt("B")]
    assert not classify(event(markets=bad), bad).collectable


# ---------------------------------------------------------------------------
# Reading the two sides
# ---------------------------------------------------------------------------


def test_sides_are_read_independently():
    """Collapsing to one 'best price' would discard the comparison that matters."""
    markets = [mkt("A", ask=60, bid=40), mkt("B", ask=45, bid=30)]
    fam = classify(event(markets=markets), markets)
    assert fam.ask_sum_cents == pytest.approx(105)
    assert fam.bid_sum_cents == pytest.approx(70)


def test_absent_side_reported_as_zero_is_not_a_quote():
    """Kalshi sends "0.0000" for an empty side rather than omitting the field."""
    markets = [mkt("A", ask=60), mkt("B", ask=40)]
    fam = classify(event(markets=markets), markets)
    assert fam.ask_sum_cents == pytest.approx(100)
    assert fam.bid_sum_cents is None


def test_legacy_field_names_still_parse():
    markets = [legacy_mkt("A", ask=60, bid=58), legacy_mkt("B", ask=41, bid=39)]
    fam = classify(event(markets=markets), markets)
    assert fam.ask_sum_cents == pytest.approx(101)


def test_unit_suffixed_fields_win_over_legacy_names():
    m = mkt("A", ask=60, bid=58)
    m["yes_ask"] = 99  # stale bare field carrying a different value
    other = mkt("B", ask=40, bid=38)
    assert classify(event(markets=[m, other]), [m, other]).ask_sum_cents == pytest.approx(100)


def test_one_cent_leg_not_mistaken_for_one_dollar():
    """Regression: a lone `1` is ambiguous between 1c and $1.00.

    Deciding the unit per value read a 1c tail bucket as a dollar, inflating it
    to 100c and turning a clean 100c partition into 199c. The unit has to be
    settled once for the whole family, on legacy fields where it is not stated.
    """
    markets = [legacy_mkt(f"M{i}", ask=p) for i, p in enumerate((60, 25, 10, 4, 1))]
    assert classify(event(markets=markets), markets).ask_sum_cents == pytest.approx(100.0)


def test_legacy_sub_dollar_family_read_as_dollars():
    markets = [legacy_mkt("A", ask=0.55), legacy_mkt("B", ask=0.45)]
    assert classify(event(markets=markets), markets).ask_sum_cents == pytest.approx(100.0)


def test_garbage_prices_ignored():
    markets = [
        {"ticker": "A", "yes_ask_dollars": "n/a"},
        {"ticker": "B", "yes_ask_dollars": "0.0000"},
    ]
    assert classify(event(markets=markets), markets).ask_sum_cents is None


# ---------------------------------------------------------------------------
# Capacity: the WORST leg decides
# ---------------------------------------------------------------------------


def test_capacity_is_the_thinnest_leg():
    """A basket is only as tradeable as its worst leg.

    Live data made the point: KXPRESPERSON-28 showed 368,438 volume and one
    contract resting at the ask.
    """
    markets = [mkt("A", ask=50, bid=48, ask_size="900.00", bid_size="800.00"),
               mkt("B", ask=50, bid=48, ask_size="3.00", bid_size="7.00")]
    fam = classify(event(markets=markets), markets)
    assert fam.min_ask_size == pytest.approx(3.0)
    assert fam.min_bid_size == pytest.approx(7.0)


def test_volume_is_history_and_capacity_is_now():
    markets = [mkt("A", ask=50, bid=48, volume="368438.00", ask_size="1.00"),
               mkt("B", ask=50, bid=48, volume="368438.00", ask_size="1.00")]
    fam = classify(event(markets=markets), markets)
    assert fam.min_volume == pytest.approx(368438.0)
    assert fam.min_ask_size == pytest.approx(1.0)


def test_capacity_filter_rejects_a_thin_leg():
    thin = event("THIN", markets=[mkt("A", ask=50, bid=48, ask_size="900.00"),
                                  mkt("B", ask=50, bid=48, ask_size="3.00")])
    selected, dropped = discover(_Client([thin]), min_ask_size=50)
    assert selected == []
    assert any("min ask size" in r for r in dropped)


def test_volume_filter_uses_the_worst_leg():
    busy = event("BUSY", markets=[mkt("A", ask=50, bid=48, volume="99999.00"),
                                  mkt("B", ask=50, bid=48, volume="0.00")])
    selected, dropped = discover(_Client([busy]), min_volume=10)
    assert selected == []
    assert any("min volume" in r for r in dropped)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def _two_families():
    return [
        event("KXFEDDECISION-26SEP", series="KXFEDDECISION",
              markets=[mkt("F1", ask=71, bid=69, volume="900.00"),
                       mkt("F2", ask=31, bid=29, volume="900.00")]),
        event("KXHIGHTNYC-26AUG", series="KXHIGHTNYC",
              markets=[mkt("W1", ask=56, bid=54, volume="50.00"),
                       mkt("W2", ask=46, bid=44, volume="50.00")]),
    ]


def test_series_filter_matches_event_prefix():
    selected, _ = discover(_Client(_two_families()), series=["KXFEDDECISION"])
    assert [f.event_ticker for f in selected] == ["KXFEDDECISION-26SEP"]


def test_series_filter_is_case_insensitive():
    assert len(discover(_Client(_two_families()), series=["kxfeddecision"])[0]) == 1


def test_event_filter_selects_exactly_one():
    selected, _ = discover(_Client(_two_families()), events=["KXHIGHTNYC-26AUG"])
    assert [f.event_ticker for f in selected] == ["KXHIGHTNYC-26AUG"]


def test_status_filter_narrows_to_one_class():
    families = _two_families() + [
        event("ARB", markets=[mkt("A", ask=70, bid=68), mkt("B", ask=40, bid=38)])
    ]
    selected, _ = discover(_Client(families), statuses=["short_arb"])
    assert [f.event_ticker for f in selected] == ["ARB"]


def test_results_sorted_by_volume():
    selected, _ = discover(_Client(_two_families()))
    assert selected[0].event_ticker == "KXFEDDECISION-26SEP"


def test_max_markets_cap_keeps_busiest_first():
    selected, dropped = discover(_Client(_two_families()), max_markets=2)
    assert [f.event_ticker for f in selected] == ["KXFEDDECISION-26SEP"]
    assert dropped.get("over max-markets cap") == 1


def test_truncation_is_reported_not_silent():
    """A silent cap reads as 'covered everything' when it did not."""
    assert "over max-markets cap" in discover(_Client(_two_families()), max_markets=2)[1]


def test_drop_reasons_are_tallied():
    events = [
        event("A", me=False, markets=[mkt("x", ask=50, bid=48), mkt("y", ask=50, bid=48)]),
        event("B", markets=[mkt("x"), mkt("y")]),
    ]
    _, dropped = discover(_Client(events))
    assert sum(dropped.values()) == 2
    assert "not flagged mutually exclusive" in dropped
    assert "no quotes" in dropped


def test_tickers_of_flattens_families():
    selected, _ = discover(_Client(_two_families()))
    assert sorted(tickers_of(selected)) == ["F1", "F2", "W1", "W2"]


def test_empty_universe_returns_empty_not_error():
    assert discover(_Client([])) == ([], {})


# ---------------------------------------------------------------------------
# Fees decide the tradeable universe
#
# The taker fee rounds up to a whole cent per leg against a fixed 100c payout,
# so an N-leg basket owes at least N cents. Past roughly ten legs no dislocation
# can close the gap. This is the constraint that actually bounds the strategy.
# ---------------------------------------------------------------------------


def test_basket_fee_scales_with_leg_count():
    small = [mkt(f"S{i}", ask=50, bid=48) for i in range(2)]
    big = [mkt(f"B{i}", ask=2, bid=1) for i in range(50)]
    assert classify(event(markets=small), small).basket_fee_cents == pytest.approx(4)
    assert classify(event(markets=big), big).basket_fee_cents == pytest.approx(50)


def test_short_gap_accounts_for_fees_not_just_distance_from_hundred():
    """A 50-leg family 1c from 100c is 50c from tradeable.

    Ranking on raw distance from 100c would put it top of the list.
    """
    big = [mkt(f"B{i}", ask=2, bid=99 / 50) for i in range(50)]
    fam = classify(event(markets=big), big)
    assert fam.bid_sum_cents == pytest.approx(99, abs=0.5)
    assert fam.short_gap_cents == pytest.approx(51, abs=1)


def test_lopsided_family_is_cheaper_than_even_one():
    """Fee is proportional to p(1-p), so it is smallest at the tails."""
    even = [mkt("A", ask=50, bid=48), mkt("B", ask=50, bid=48)]
    lopsided = [mkt("A", ask=95, bid=93), mkt("B", ask=5, bid=3)]
    assert (classify(event(markets=lopsided), lopsided).basket_fee_cents
            < classify(event(markets=even), even).basket_fee_cents)


def test_oversized_field_is_dropped_by_leg_count():
    big = event("PGA", markets=[mkt(f"B{i}", ask=2, bid=1) for i in range(50)])
    selected, dropped = discover(_Client([big]), max_legs=10)
    assert selected == []
    assert any("legs" in r for r in dropped)


def test_leg_cap_can_be_disabled():
    big = event("PGA", markets=[mkt(f"B{i}", ask=2, bid=1) for i in range(50)])
    assert len(discover(_Client([big]), max_legs=None)[0]) == 1


def test_ranking_prefers_closest_to_arbitrage_over_biggest():
    """Live selection spent 97 of 100 market slots on two untradeable fields."""
    families = [
        event("BIG", markets=[mkt(f"B{i}", ask=13, bid=12, volume="999999.00")
                              for i in range(8)]),
        event("NEAR", markets=[mkt("A", ask=53, bid=52, volume="100.00"),
                               mkt("B", ask=50, bid=49, volume="100.00")]),
    ]
    selected, _ = discover(_Client(families), max_legs=10)
    assert selected[0].event_ticker == "NEAR"
