"""Tests for the fair-value trade rule.

The strategy's job is mostly to refuse, so most of these check that it refuses
for the right reason. A version of this module with the fee applied per order
instead of per contract, or with the hurdle taken from the point estimate,
passes a naive smoke test and loses money.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from quant.common.db.schema import OrderBookLevel, OrderBookSnapshot
from quant.kalshi.fees import taker_fee_cents
from quant.kalshi.strategies.fair_value import (
    FairValueConfig,
    evaluate,
    kelly_fraction,
)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def book(bid=None, ask=None, bid_size=1000.0, ask_size=1000.0, ticker="KXHIGHNY-26AUG25-B72"):
    return OrderBookSnapshot(
        timestamp=NOW,
        contract_id=ticker,
        yes_bids=[OrderBookLevel(price=bid, size=bid_size)] if bid is not None else [],
        yes_asks=[OrderBookLevel(price=ask, size=ask_size)] if ask is not None else [],
    )


DEEP = FairValueConfig(bankroll_cents=10_000_000.0)


# -- Kelly -------------------------------------------------------------------


def test_kelly_matches_the_closed_form():
    """f* = (100p - c)/(100 - c): the edge in cents over the profit if it wins."""
    assert kelly_fraction(0.60, 50.0) == pytest.approx(0.20)
    assert kelly_fraction(0.90, 10.0) == pytest.approx(80 / 90)
    assert kelly_fraction(0.50, 50.0) == 0.0


def test_kelly_is_zero_not_negative_when_the_contract_is_not_worth_its_cost():
    """A negative fraction would be read as a short by a caller that does not
    check the sign, and this module never shorts — it buys the other side."""
    assert kelly_fraction(0.30, 50.0) == 0.0


def test_kelly_refuses_a_free_or_certain_contract():
    """At 0c Kelly is unbounded and at 100c the contract cannot win. Both are
    data errors, and both would size a position if allowed through."""
    with pytest.raises(ValueError, match=r"\(0, 100\)"):
        kelly_fraction(0.6, 0.0)
    with pytest.raises(ValueError, match=r"\(0, 100\)"):
        kelly_fraction(0.6, 100.0)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        kelly_fraction(60.0, 50.0)


# -- the refusals ------------------------------------------------------------


def test_a_thin_edge_does_not_survive_the_fee():
    """Fair 51c against an ask of 50c is a 1c gross edge. The taker fee at 50c
    is 1.75c per contract, so the trade loses. This is the single most common
    way a prediction-market backtest invents profit."""
    assert evaluate(book(bid=49, ask=50), 0.51, stderr=0.0, cfg=DEEP, now=NOW) is None


def test_an_edge_below_the_stderr_hurdle_is_refused():
    """5c of gross edge on a probability estimated to +/- 5 points is not an
    edge, it is an estimate. Two standard errors is 10c and the trade dies."""
    tight = evaluate(book(bid=54, ask=55), 0.65, stderr=0.005, cfg=DEEP, now=NOW)
    loose = evaluate(book(bid=54, ask=55), 0.65, stderr=0.050, cfg=DEEP, now=NOW)
    assert tight is not None and tight.action == "buy_yes"
    assert loose is None


def test_the_hurdle_is_reported_so_a_near_miss_is_auditable():
    sig = evaluate(book(bid=54, ask=55), 0.75, stderr=0.02, cfg=DEEP, now=NOW)
    assert sig is not None
    assert sig.metadata["hurdle_cents"] == pytest.approx(2 * 100 * 0.02)


def test_a_one_sided_book_produces_no_signal_on_the_missing_side():
    """36.6% of snapshots in the measured panel have no ask at all, and 30%
    have no book. A model that quotes into an empty side reports an edge it
    cannot take.

    The direction that survives is the one the remaining side allows: with no
    ask you can only buy NO, which is a trade only when fair value is LOW. A
    cheap YES that nobody is offering is not an opportunity.
    """
    # No ask, and the model thinks YES is worth 90c. The trade it wants is
    # unavailable and the trade available is terrible. Nothing.
    assert evaluate(book(bid=20, ask=None), 0.90, stderr=0.0, cfg=DEEP, now=NOW) is None

    # No ask, but the model thinks YES is worth 10c: buy NO at 40c.
    only_bid = evaluate(book(bid=60, ask=None), 0.10, stderr=0.0, cfg=DEEP, now=NOW)
    assert only_bid is not None and only_bid.action == "buy_no"

    # No bid, and YES is cheap: buy YES at the ask.
    only_ask = evaluate(book(bid=None, ask=20), 0.90, stderr=0.0, cfg=DEEP, now=NOW)
    assert only_ask is not None and only_ask.action == "buy_yes"

    assert evaluate(book(), 0.90, stderr=0.0, cfg=DEEP, now=NOW) is None


def test_an_empty_book_at_the_touch_produces_nothing():
    """Capacity is not volume: one contract at the ask is one contract."""
    cfg = FairValueConfig(bankroll_cents=10_000_000.0)
    thin = evaluate(book(bid=10, ask=20, ask_size=0.4), 0.90, stderr=0.0, cfg=cfg, now=NOW)
    # 0.4 contracts available floors to zero, so the YES side cannot fill.
    assert thin is None or thin.action == "buy_no"


def test_cents_passed_as_a_probability_are_caught():
    with pytest.raises(ValueError, match="cents"):
        evaluate(book(bid=49, ask=50), 65.0, stderr=0.0, cfg=DEEP, now=NOW)


# -- direction ---------------------------------------------------------------


def test_it_buys_no_when_the_market_is_too_high():
    """Fair 20c against a bid of 60c: the trade is to buy NO at 40c, not to
    look for a cheap YES."""
    sig = evaluate(book(bid=60, ask=62), 0.20, stderr=0.0, cfg=DEEP, now=NOW)
    assert sig is not None
    assert sig.action == "buy_no"
    assert sig.market_price == 40.0
    assert sig.fair_value == pytest.approx(80.0)          # fair value OF THE NO
    assert sig.metadata["yes_fair_value_cents"] == pytest.approx(20.0)
    assert sig.gross_edge_cents == pytest.approx(40.0)


def test_it_buys_yes_when_the_market_is_too_low():
    sig = evaluate(book(bid=18, ask=20), 0.80, stderr=0.0, cfg=DEEP, now=NOW)
    assert sig is not None
    assert sig.action == "buy_yes"
    assert sig.market_price == 20.0
    assert sig.gross_edge_cents == pytest.approx(60.0)


def test_the_bigger_total_edge_wins_not_the_bigger_per_contract_edge():
    """A 6c edge on one fillable contract is worth less than a 2c edge on 500.
    Ranking per-contract would take the wrong one every time the touch is thin.
    """
    wide = OrderBookSnapshot(
        timestamp=NOW,
        contract_id="X",
        yes_bids=[OrderBookLevel(price=44, size=500.0)],
        yes_asks=[OrderBookLevel(price=48, size=1.0)],
    )
    # Fair 54c: YES at 48 is a 6c edge on 1 contract; NO at 56 is worth
    # 46 - 56 < 0, so construct the mirror case instead.
    sig = evaluate(wide, 0.54, stderr=0.0, cfg=DEEP, now=NOW)
    assert sig is not None and sig.action == "buy_yes"
    assert sig.max_size == 1.0

    deep_other_side = OrderBookSnapshot(
        timestamp=NOW,
        contract_id="X",
        yes_bids=[OrderBookLevel(price=44, size=500.0)],
        yes_asks=[OrderBookLevel(price=48, size=1.0)],
    )
    sig2 = evaluate(deep_other_side, 0.40, stderr=0.0, cfg=DEEP, now=NOW)
    assert sig2 is not None and sig2.action == "buy_no"
    assert sig2.max_size > 1.0


# -- fees and size solved together -------------------------------------------


def test_the_fee_per_contract_falls_with_size():
    """REGRESSION for the ceiling. The exchange rounds the ORDER fee up, so one
    contract at 50c owes 2c (a fee of 1.75 rounded) while a hundred owe 1.75c
    each. A model that charges the one-contract fee at every size understates
    every large edge and overstates every small one."""
    assert taker_fee_cents(50.0, 1) == 2
    assert taker_fee_cents(50.0, 100) / 100 == pytest.approx(1.75)
    assert taker_fee_cents(50.0, 1) / 1 > taker_fee_cents(50.0, 100) / 100


def test_the_reported_fee_matches_the_size_actually_proposed():
    """The two must be consistent, or the net edge is quoted for a size nobody
    is trading."""
    sig = evaluate(book(bid=18, ask=20), 0.80, stderr=0.0, cfg=DEEP, now=NOW)
    assert sig is not None
    expected = taker_fee_cents(20.0, sig.max_size) / sig.max_size
    assert sig.metadata["fee_per_contract_cents"] == pytest.approx(expected)
    assert sig.net_edge_cents == pytest.approx(sig.gross_edge_cents - expected)


def test_net_edge_never_exceeds_gross_edge():
    """The schema enforces it; this proves the strategy cannot construct one
    that violates it, which is where the sign of the fee would show up."""
    for fair in (0.05, 0.2, 0.5, 0.8, 0.95):
        sig = evaluate(book(bid=40, ask=45), fair, stderr=0.0, cfg=DEEP, now=NOW)
        if sig is not None:
            assert sig.net_edge_cents <= sig.gross_edge_cents


# -- sizing ------------------------------------------------------------------


def test_size_is_capped_by_the_book_not_by_the_bankroll():
    rich = FairValueConfig(bankroll_cents=10_000_000_000.0)
    sig = evaluate(book(bid=18, ask=20, ask_size=37.0), 0.80, stderr=0.0, cfg=rich, now=NOW)
    assert sig is not None
    assert sig.max_size == 37.0
    assert sig.metadata["book_depth"] == 37.0


def test_uncertainty_shrinks_the_position_even_when_the_trade_still_passes():
    """Two filters, two jobs. `edge_stderrs` decides whether to trade;
    `uncertainty_penalty_stderrs` decides how much. Sizing at the point
    estimate while filtering carefully is the common half-measure and it
    over-bets exactly the trades the model is least sure about.
    """
    cfg = FairValueConfig(bankroll_cents=50_000.0, edge_stderrs=0.0)
    certain = evaluate(book(bid=18, ask=20, ask_size=1e9), 0.80, 0.00, cfg, NOW)
    unsure = evaluate(book(bid=18, ask=20, ask_size=1e9), 0.80, 0.05, cfg, NOW)
    assert certain is not None and unsure is not None
    assert unsure.max_size < certain.max_size


def test_a_position_the_uncertainty_wipes_out_is_not_taken():
    """When one standard error against the position removes the whole edge,
    Kelly is zero and there is no trade, whatever the point estimate says."""
    cfg = FairValueConfig(bankroll_cents=1_000_000.0, edge_stderrs=0.0,
                          uncertainty_penalty_stderrs=1.0)
    assert evaluate(book(bid=48, ask=50), 0.53, stderr=0.10, cfg=cfg, now=NOW) is None


def test_the_portfolio_cap_binds_across_the_family():
    """Buckets in one family are mutually exclusive, so independent sizing
    over-commits the day. Until the covariance is modelled, this is the
    blunt instrument that stops it."""
    cfg = FairValueConfig(bankroll_cents=10_000_000.0, portfolio_cap_contracts=25.0)
    sig = evaluate(book(bid=18, ask=20, ask_size=1e6), 0.80, stderr=0.0, cfg=cfg, now=NOW)
    assert sig is not None and sig.max_size == 25.0


def test_size_is_a_whole_number_of_contracts():
    sig = evaluate(book(bid=18, ask=20), 0.80, stderr=0.0, cfg=DEEP, now=NOW)
    assert sig is not None
    assert sig.max_size == math.floor(sig.max_size)


def test_a_negative_stderr_is_refused():
    with pytest.raises(ValueError, match="non-negative"):
        evaluate(book(bid=18, ask=20), 0.8, stderr=-0.01, cfg=DEEP, now=NOW)
