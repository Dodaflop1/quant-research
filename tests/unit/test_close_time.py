"""Tests for close-time parsing and the panel-lifetime filter.

Motivation, from real data: the universe pinned on 2026-08-24 was selected on
volume alone. 38 of its 150 tickers had **already settled**, another 20 settled
within a week, and only about a third would have survived the six-week
collection window. Kalshi's volume is concentrated in same-day sports, so
"busiest" and "longest-lived" point in opposite directions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quant.ingest.discovery import classify, discover
from tests.unit.test_discovery import _Client, event, mkt


def at(days: float) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(days=days)
    ).isoformat().replace("+00:00", "Z")


def dated_mkt(ticker, days, **kw):
    m = mkt(ticker, **kw)
    m["close_time"] = at(days)
    return m


# -- parsing ----------------------------------------------------------------


def test_parses_iso_close_time():
    fam = classify(event(), [dated_mkt("A", 30, ask=50, bid=49), dated_mkt("B", 30, ask=52, bid=51)])
    assert fam.closes_at is not None
    assert fam.days_to_close == pytest.approx(30, abs=0.01)


def test_parses_a_unix_close_time():
    when = datetime.now(timezone.utc) + timedelta(days=10)
    markets = [mkt("A", ask=50, bid=49), mkt("B", ask=52, bid=51)]
    for m in markets:
        m["close_time"] = int(when.timestamp())
    fam = classify(event(), markets)
    assert fam.days_to_close == pytest.approx(10, abs=0.01)


def test_falls_back_through_the_alternative_field_names():
    markets = [mkt("A", ask=50, bid=49), mkt("B", ask=52, bid=51)]
    for m in markets:
        m["expiration_time"] = at(15)
    fam = classify(event(), markets)
    assert fam.days_to_close == pytest.approx(15, abs=0.01)


def test_absent_close_time_is_none_not_zero():
    """None means unknown. Zero would mean 'settles now', which is a real claim."""
    fam = classify(event(), [mkt("A", ask=50, bid=49), mkt("B", ask=52, bid=51)])
    assert fam.closes_at is None
    assert fam.days_to_close is None


def test_an_unparseable_close_time_does_not_raise():
    markets = [mkt("A", ask=50, bid=49), mkt("B", ask=52, bid=51)]
    for m in markets:
        m["close_time"] = "not a date"
    assert classify(event(), markets).closes_at is None


# -- the family's lifetime is its SOONEST leg -------------------------------


def test_family_close_time_is_the_soonest_leg_not_the_latest():
    """A basket needs every leg live at once.

    The moment the first leg settles the basket stops being tradeable and the
    series stops being complete, so the family's usable lifetime is the minimum
    across legs. Taking the maximum would let a family with one same-day leg
    masquerade as a month-long series.
    """
    fam = classify(
        event(),
        [
            dated_mkt("A", 90, ask=50, bid=49),
            dated_mkt("B", 2, ask=52, bid=51),
            dated_mkt("C", 45, ask=51, bid=50),
        ],
    )
    assert fam.days_to_close == pytest.approx(2, abs=0.01)


def test_a_settled_leg_gives_a_negative_lifetime():
    fam = classify(
        event(), [dated_mkt("A", -3, ask=50, bid=49), dated_mkt("B", 30, ask=52, bid=51)]
    )
    assert fam.days_to_close < 0


# -- the filter -------------------------------------------------------------


def _two_families():
    return [
        event(
            ticker="LONG",
            markets=[dated_mkt("L-A", 60, ask=50, bid=49), dated_mkt("L-B", 60, ask=52, bid=51)],
        ),
        event(
            ticker="SHORT",
            markets=[dated_mkt("S-A", 1, ask=50, bid=49), dated_mkt("S-B", 1, ask=52, bid=51)],
        ),
    ]


def test_filter_keeps_only_families_that_outlive_the_window():
    families, dropped = discover(_Client(_two_families()), min_days_to_close=45)
    assert [f.event_ticker for f in families] == ["LONG"]
    assert any("closes in under" in reason for reason in dropped)


def test_zero_disables_the_filter():
    """The default must preserve the cross-sectional behaviour."""
    families, _ = discover(_Client(_two_families()), min_days_to_close=0)
    assert {f.event_ticker for f in families} == {"LONG", "SHORT"}


def test_a_family_with_no_close_time_is_dropped_when_the_filter_is_on():
    """Unknown lifetime is treated as too short, and counted.

    Keeping it would silently readmit exactly what the filter exists to
    exclude, and the tally is what makes the decision visible.
    """
    events = [event(ticker="NODATE", markets=[mkt("A", ask=50, bid=49), mkt("B", ask=52, bid=51)])]
    families, dropped = discover(_Client(events), min_days_to_close=45)
    assert families == []
    assert dropped.get("no close time in payload") == 1


def test_an_already_settled_family_is_dropped():
    events = [
        event(
            ticker="GONE",
            markets=[dated_mkt("A", -5, ask=50, bid=49), dated_mkt("B", -5, ask=52, bid=51)],
        )
    ]
    families, _ = discover(_Client(events), min_days_to_close=1)
    assert families == []
