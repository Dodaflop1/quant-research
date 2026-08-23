"""Tests for the Kalshi fee model.

Checked against the published formula, not against a live fill. The rounding
granularity and the per-series multiplier table still need reconciling against
a settled trade before this is used to size anything.
"""

from decimal import ROUND_CEILING, Decimal

import pytest

from quant.kalshi.fees import (
    MAKER_RATE,
    TAKER_RATE,
    basket_taker_fee_cents,
    bucket_sum_edge_cents,
    maker_fee_cents,
    taker_fee_cents,
)


def _expected(rate: Decimal, price_cents, contracts, multiplier=1) -> int:
    """Independent restatement of the published formula, in exact arithmetic."""
    p = Decimal(price_cents) / Decimal(100)
    fee = Decimal(multiplier) * rate * Decimal(contracts) * p * (Decimal(1) - p) * Decimal(100)
    return int(fee.to_integral_value(rounding=ROUND_CEILING))


@pytest.mark.parametrize("price", [1, 10, 25, 50, 75, 90, 99])
def test_taker_matches_published_formula(price):
    assert taker_fee_cents(price, 100) == _expected(TAKER_RATE, price, 100)


def test_maker_rate_is_a_quarter_of_taker():
    assert MAKER_RATE == TAKER_RATE / 4


def test_maker_multiplier_defaults_to_zero():
    """Maker fees apply only where the schedule specifies a multiplier."""
    assert maker_fee_cents(50, 1000) == 0
    assert maker_fee_cents(50, 1000, multiplier=1.0) > 0


def test_fee_is_maximised_at_fifty_cents():
    fees = {p: taker_fee_cents(p, 10_000) for p in range(1, 100)}
    assert max(fees, key=fees.get) == 50


@pytest.mark.parametrize("p", [1, 5, 20, 35, 49])
def test_fee_is_symmetric_about_fifty(p):
    """p(1-p) is symmetric, so binary float error must not break the identity."""
    assert taker_fee_cents(p, 10_000) == taker_fee_cents(100 - p, 10_000)


def test_no_float_rounding_artefact_at_twenty_cents():
    """Regression: 20/100 * 80/100 is 0.16000000000000003 in float, and the
    exact fee of 11200c ceilings to 11201c."""
    assert taker_fee_cents(20, 10_000) == 11_200


def test_fee_vanishes_at_the_tails():
    """Cost is wildly non-uniform across a basket's legs; this is why."""
    assert taker_fee_cents(50, 100) > 10 * taker_fee_cents(1, 100)


def test_fee_rounds_up_never_down():
    # 0.07 * 1 * 0.5 * 0.5 = $0.0175 = 1.75c, which must round up to 2c.
    assert taker_fee_cents(50, 1) == 2
    # A single contract at 1c costs 0.0693c, which still rounds up to 1c: there
    # is no free trade, and per-leg minimums dominate cheap baskets.
    assert taker_fee_cents(1, 1) == 1


def test_zero_contracts_is_free():
    assert taker_fee_cents(50, 0) == 0


@pytest.mark.parametrize("bad", [-1, 101])
def test_price_out_of_range_rejected(bad):
    with pytest.raises(ValueError):
        taker_fee_cents(bad, 1)


def test_negative_contracts_rejected():
    with pytest.raises(ValueError):
        taker_fee_cents(50, -1)


def test_series_multiplier_scales_fee():
    base = taker_fee_cents(50, 1000, multiplier=1.0)
    assert taker_fee_cents(50, 1000, multiplier=0.0) == 0
    assert taker_fee_cents(50, 1000, multiplier=2.0) == pytest.approx(2 * base, rel=0.01)


# ---------------------------------------------------------------------------
# Basket / bucket-sum economics
# ---------------------------------------------------------------------------


def test_basket_fee_is_charged_per_leg():
    legs = [50, 50]
    assert basket_taker_fee_cents(legs, 100) == 2 * taker_fee_cents(50, 100)


def test_bucket_sum_edge_is_net_of_fees():
    # Five legs summing to 98c: 2c gross before fees.
    r = bucket_sum_edge_cents([85, 5, 4, 3, 1], contracts=1)
    assert r["price_sum_cents"] == 98
    assert r["gross_edge_cents"] == pytest.approx(2.0)
    assert r["net_edge_cents"] < r["gross_edge_cents"]


def test_deviation_can_be_positive_yet_untradeable():
    """The go/no-go the whole project turns on."""
    # A 1c deviation across many mid-priced legs does not survive fees.
    legs = [45, 45, 9]
    r = bucket_sum_edge_cents(legs, contracts=1)
    assert r["gross_edge_cents"] == pytest.approx(1.0)
    assert r["net_edge_cents"] < 0


def test_tail_heavy_basket_is_cheaper_than_mid_heavy_at_equal_deviation():
    """Ranking opportunities by raw deviation over-selects the expensive ones."""
    tail = bucket_sum_edge_cents([96, 1, 1], contracts=1)
    mid = bucket_sum_edge_cents([49, 49], contracts=1)
    assert tail["gross_edge_cents"] == pytest.approx(mid["gross_edge_cents"])
    assert tail["fee_cents"] < mid["fee_cents"]
    assert tail["net_edge_cents"] > mid["net_edge_cents"]


def test_breakeven_price_sum_is_below_one_hundred():
    r = bucket_sum_edge_cents([50, 48], contracts=1)
    assert r["breakeven_price_sum_cents"] < 100


def test_empty_basket_rejected():
    with pytest.raises(ValueError):
        bucket_sum_edge_cents([])
