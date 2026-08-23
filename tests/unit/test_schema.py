"""Tests for the shared data schemas.

These cover the invariants that are easy to get wrong and expensive to discover
late: the Hawkes branching ratio, cross-field OHLC consistency, order book
ordering, and UTC enforcement.
"""

import math
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from quant.common.db.schema import (
    AbnormalReturn,
    BacktestOrder,
    CalibrationResult,
    HawkesParameters,
    MarketData,
    OrderBookLevel,
    OrderBookSnapshot,
    Position,
    Settlement,
    StrategySignal,
)

UTC = timezone.utc
T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Hawkes parameterisation
# ---------------------------------------------------------------------------


def _params(**overrides) -> HawkesParameters:
    kwargs = dict(
        mu=0.5,
        alpha=0.8,
        beta=2.0,
        time_unit="minute",
        log_likelihood=-1234.5,
        n_events=500,
        observation_window=1440.0,
        converged=True,
    )
    kwargs.update(overrides)
    return HawkesParameters(**kwargs)


def test_branching_ratio_is_alpha_over_beta():
    """n = integral of the kernel = alpha / beta. mu must not appear."""
    p = _params(alpha=0.8, beta=2.0)
    assert p.branching_ratio == pytest.approx(0.4)


def test_branching_ratio_is_independent_of_baseline():
    """The baseline governs immigrant arrivals, not offspring per event."""
    low = _params(mu=0.01)
    high = _params(mu=100.0)
    assert low.branching_ratio == pytest.approx(high.branching_ratio)


def test_stationarity_boundary():
    assert _params(alpha=0.99, beta=1.0).is_stationary
    assert not _params(alpha=1.0, beta=1.0).is_stationary
    assert not _params(alpha=1.5, beta=1.0).is_stationary


def test_half_life_from_decay_rate():
    p = _params(beta=0.5)
    assert p.excitation_half_life == pytest.approx(math.log(2) / 0.5)


def test_expected_cluster_size():
    p = _params(alpha=0.5, beta=1.0)  # n = 0.5
    assert p.expected_cluster_size == pytest.approx(2.0)
    assert _params(alpha=2.0, beta=1.0).expected_cluster_size is None


def test_non_stationary_fit_is_representable():
    """An explosive fit is a diagnostic, not a validation error."""
    p = _params(alpha=3.0, beta=1.0)
    assert p.branching_ratio == pytest.approx(3.0)
    assert not p.is_stationary


def test_branching_ratio_cannot_drift_from_parameters():
    """It is derived, so there is no stored field to fall out of sync."""
    assert "branching_ratio" not in HawkesParameters.model_fields


@pytest.mark.parametrize("bad", [{"alpha": 0}, {"beta": 0}, {"mu": -1}])
def test_rejects_nonpositive_parameters(bad):
    with pytest.raises(ValidationError):
        _params(**bad)


# ---------------------------------------------------------------------------
# OHLC cross-field validation (previously dead code)
# ---------------------------------------------------------------------------


def _bar(**overrides) -> MarketData:
    kwargs = dict(
        timestamp=T0,
        ticker="AAPL",
        interval="1min",
        open=150.0,
        high=151.0,
        low=149.0,
        close=150.5,
        volume=10_000,
    )
    kwargs.update(overrides)
    return MarketData(**kwargs)


def test_valid_bar_accepted():
    assert _bar().close == 150.5


def test_high_below_low_rejected():
    with pytest.raises(ValidationError, match="high"):
        _bar(high=148.0, low=149.0)


def test_open_outside_range_rejected():
    with pytest.raises(ValidationError, match="open"):
        _bar(open=200.0)


def test_close_outside_range_rejected():
    with pytest.raises(ValidationError, match="close"):
        _bar(close=100.0)


def test_crossed_quote_rejected():
    with pytest.raises(ValidationError, match="crossed"):
        _bar(bid=151.0, ask=150.0)


# ---------------------------------------------------------------------------
# UTC enforcement
# ---------------------------------------------------------------------------


def test_naive_timestamp_rejected():
    with pytest.raises(ValidationError, match="timezone-aware"):
        _bar(timestamp=datetime(2026, 8, 23, 12, 0))


def test_non_utc_timestamp_normalised():
    eastern = timezone(timedelta(hours=-4))
    bar = _bar(timestamp=datetime(2026, 8, 23, 8, 0, tzinfo=eastern))
    assert bar.timestamp == T0
    assert bar.timestamp.tzinfo == UTC


# ---------------------------------------------------------------------------
# Order book
# ---------------------------------------------------------------------------


def _book(**overrides) -> OrderBookSnapshot:
    kwargs = dict(
        timestamp=T0,
        contract_id="FED-MAR26-B4",
        yes_bids=[
            OrderBookLevel(price=64, size=100),
            OrderBookLevel(price=63, size=250),
        ],
        yes_asks=[
            OrderBookLevel(price=66, size=80),
            OrderBookLevel(price=67, size=400),
        ],
    )
    kwargs.update(overrides)
    return OrderBookSnapshot(**kwargs)


def test_book_top_and_spread():
    b = _book()
    assert b.best_yes_bid == 64
    assert b.best_yes_ask == 66
    assert b.spread == 2
    assert b.yes_mid == 65


def test_empty_side_gives_none_not_zero():
    b = _book(yes_asks=[])
    assert b.best_yes_ask is None
    assert b.spread is None
    assert b.yes_mid is None


def test_misordered_bids_rejected():
    with pytest.raises(ValidationError, match="descending"):
        _book(yes_bids=[OrderBookLevel(price=63, size=1), OrderBookLevel(price=64, size=1)])


def test_crossed_book_rejected():
    with pytest.raises(ValidationError, match="crossed"):
        _book(
            yes_bids=[OrderBookLevel(price=70, size=1)],
            yes_asks=[OrderBookLevel(price=66, size=1)],
        )


def test_no_side_is_complement_of_yes_side():
    b = _book()
    assert [lvl.price for lvl in b.no_bids()] == [34, 33]
    assert [lvl.price for lvl in b.no_asks()] == [36, 37]


def test_depth_up_to_caps_arbitrage_size():
    b = _book()
    assert b.depth_up_to("yes_ask", 66) == 80
    assert b.depth_up_to("yes_ask", 67) == 480
    assert b.depth_up_to("yes_bid", 64) == 100
    assert b.depth_up_to("yes_bid", 63) == 350


# ---------------------------------------------------------------------------
# Signals, orders, positions, settlement
# ---------------------------------------------------------------------------


def test_net_edge_cannot_exceed_gross_edge():
    with pytest.raises(ValidationError, match="net edge"):
        StrategySignal(
            timestamp=T0,
            strategy="bucket_sum",
            contract_id="X",
            action="buy_yes",
            market_price=40,
            gross_edge_cents=2.0,
            net_edge_cents=3.0,
            max_size=100,
        )


def test_filled_order_requires_fill_price():
    with pytest.raises(ValidationError, match="fill_price"):
        BacktestOrder(
            order_id="1",
            timestamp=T0,
            contract_id="X",
            side="buy",
            quantity=10,
            limit_price=50,
            filled_quantity=10,
            fill_price=None,
            fee_cents=1.0,
            status="filled",
        )


def test_overfill_rejected():
    with pytest.raises(ValidationError, match="exceeds requested"):
        BacktestOrder(
            order_id="1",
            timestamp=T0,
            contract_id="X",
            side="buy",
            quantity=10,
            limit_price=50,
            filled_quantity=11,
            fill_price=50,
            fee_cents=1.0,
            status="filled",
        )


def test_short_position_unrealised_pnl_sign():
    short = Position(
        contract_id="X",
        quantity=-100,
        avg_entry_price=60,
        mark_price=55,
        realised_pnl_cents=0,
    )
    assert short.unrealised_pnl_cents == pytest.approx(500.0)


def test_settlement_payout():
    assert Settlement(contract_id="X", settled_at=T0, outcome="yes").payout == 100.0
    assert Settlement(contract_id="X", settled_at=T0, outcome="no").payout == 0.0
    assert Settlement(contract_id="X", settled_at=T0, outcome="voided").payout is None


def test_abnormal_return_window_must_be_ordered():
    with pytest.raises(ValidationError, match="window_end_min"):
        AbnormalReturn(
            event_time=T0,
            ticker="AAPL",
            window_start_min=30,
            window_end_min=0,
            stock_return=0.01,
            expected_return=0.002,
            abnormal_return=0.008,
            benchmark="market_model",
        )


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def _calib(**overrides) -> CalibrationResult:
    kwargs = dict(
        name="fed_rate_v1",
        test_start=T0,
        test_end=T0 + timedelta(days=90),
        n_forecasts=400,
        base_rate=0.5,
        brier_score=0.2,
        log_loss=0.6,
    )
    kwargs.update(overrides)
    return CalibrationResult(**kwargs)


def test_no_protected_namespace_collision():
    """`model_name` would collide with pydantic v2's protected `model_` prefix."""
    c = _calib()
    assert c.name == "fed_rate_v1"
    assert "model_name" not in CalibrationResult.model_fields


def test_uncertainty_and_skill_score():
    c = _calib(base_rate=0.5, brier_score=0.2)
    assert c.uncertainty == pytest.approx(0.25)
    assert c.skill_vs_base_rate == pytest.approx(0.2)


def test_no_skill_forecaster_scores_zero():
    c = _calib(base_rate=0.5, brier_score=0.25)
    assert c.skill_vs_base_rate == pytest.approx(0.0)


def test_worse_than_base_rate_is_negative():
    assert _calib(base_rate=0.5, brier_score=0.30).skill_vs_base_rate < 0


def test_inverted_test_window_rejected():
    with pytest.raises(ValidationError, match="test_end"):
        _calib(test_start=T0, test_end=T0 - timedelta(days=1))
