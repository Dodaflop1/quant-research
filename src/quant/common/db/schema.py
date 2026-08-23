"""
Pydantic data models for the Kalshi mispricing engine and the information
diffusion model.

Conventions used throughout:

* All timestamps are timezone-aware UTC. Naive datetimes are rejected.
* All Kalshi prices are in cents on [0, 100] and refer to the YES side unless
  stated otherwise.
* Models are frozen. Once a record is validated it does not change, which keeps
  backtest state explicit rather than mutated in place.
* Quantities that can be derived from other fields are exposed as properties,
  not stored, so they cannot drift out of sync with the fields they come from.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

FROZEN = ConfigDict(frozen=True)


def _require_utc(value: datetime, field_name: str) -> datetime:
    """Reject naive datetimes and normalise to UTC.

    Mixing naive and aware timestamps is the most common source of silent
    off-by-hours errors in event studies, so it is rejected at the boundary.
    """
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware (UTC)")
    return value.astimezone(timezone.utc)


# =============================================================================
# Kalshi
# =============================================================================


class OrderBookLevel(BaseModel):
    """A single price level in an order book."""

    price: float = Field(..., ge=0, le=100, description="Price in cents")
    size: float = Field(..., gt=0, description="Contracts resting at this price")

    model_config = FROZEN


class OrderBookSnapshot(BaseModel):
    """Full-depth order book for one contract at one instant.

    The book is expressed entirely in YES terms. On Kalshi a resting NO bid at
    price ``q`` is economically the same as a YES ask at ``100 - q``, so storing
    both sides independently invites inconsistency. NO-side views are derived via
    :meth:`no_bids` / :meth:`no_asks`.

    This complementarity should be re-verified against live API payloads before
    the ingestion layer is trusted; it is an assumption about Kalshi's book
    representation, not something this schema can enforce.

    Full depth (rather than top-of-book only) is required because the structural
    arbitrage sizing step needs to know how many contracts can actually be lifted
    before the edge is exhausted.
    """

    timestamp: datetime = Field(..., description="UTC time the snapshot was taken")
    contract_id: str = Field(..., description="Kalshi contract identifier")
    yes_bids: list[OrderBookLevel] = Field(
        default_factory=list, description="YES bids, best (highest) first"
    )
    yes_asks: list[OrderBookLevel] = Field(
        default_factory=list, description="YES asks, best (lowest) first"
    )

    model_config = FROZEN

    @model_validator(mode="after")
    def _validate_book(self) -> "OrderBookSnapshot":
        object.__setattr__(
            self, "timestamp", _require_utc(self.timestamp, "timestamp")
        )

        bids = [lvl.price for lvl in self.yes_bids]
        asks = [lvl.price for lvl in self.yes_asks]

        if bids != sorted(bids, reverse=True):
            raise ValueError("yes_bids must be sorted descending by price")
        if asks != sorted(asks):
            raise ValueError("yes_asks must be sorted ascending by price")
        if bids and asks and bids[0] >= asks[0]:
            raise ValueError(
                f"crossed book: best bid {bids[0]} >= best ask {asks[0]}"
            )
        return self

    @property
    def best_yes_bid(self) -> Optional[float]:
        return self.yes_bids[0].price if self.yes_bids else None

    @property
    def best_yes_ask(self) -> Optional[float]:
        return self.yes_asks[0].price if self.yes_asks else None

    @property
    def yes_mid(self) -> Optional[float]:
        """Mid price, or None if either side is empty.

        Note that mid is a reporting convenience. Backtest fills must never
        assume execution at mid - see the fill model in the backtest package.
        """
        if self.best_yes_bid is None or self.best_yes_ask is None:
            return None
        return (self.best_yes_bid + self.best_yes_ask) / 2

    @property
    def spread(self) -> Optional[float]:
        if self.best_yes_bid is None or self.best_yes_ask is None:
            return None
        return self.best_yes_ask - self.best_yes_bid

    def no_bids(self) -> list[OrderBookLevel]:
        """YES asks re-expressed as NO bids."""
        return [
            OrderBookLevel(price=100 - lvl.price, size=lvl.size)
            for lvl in self.yes_asks
        ]

    def no_asks(self) -> list[OrderBookLevel]:
        """YES bids re-expressed as NO asks."""
        return [
            OrderBookLevel(price=100 - lvl.price, size=lvl.size)
            for lvl in self.yes_bids
        ]

    def depth_up_to(self, side: Literal["yes_bid", "yes_ask"], limit: float) -> float:
        """Contracts available at ``limit`` or better on the given side.

        Used by the sizing layer to cap an arbitrage trade at the quantity the
        book can actually absorb.
        """
        if side == "yes_bid":
            return sum(lvl.size for lvl in self.yes_bids if lvl.price >= limit)
        return sum(lvl.size for lvl in self.yes_asks if lvl.price <= limit)


class Trade(BaseModel):
    """A completed trade print."""

    timestamp: datetime = Field(..., description="UTC execution time")
    contract_id: str = Field(..., description="Kalshi contract identifier")
    price: float = Field(..., ge=0, le=100, description="Execution price, YES cents")
    size: float = Field(..., gt=0, description="Contracts traded")
    taker_side: Literal["buy", "sell"] = Field(
        ..., description="Side of the aggressing order, in YES terms"
    )

    model_config = FROZEN

    @model_validator(mode="after")
    def _validate(self) -> "Trade":
        object.__setattr__(self, "timestamp", _require_utc(self.timestamp, "timestamp"))
        return self


class Settlement(BaseModel):
    """Terminal settlement of a contract."""

    contract_id: str = Field(..., description="Kalshi contract identifier")
    settled_at: datetime = Field(..., description="UTC settlement time")
    outcome: Literal["yes", "no", "voided"] = Field(..., description="Resolved outcome")

    model_config = FROZEN

    @property
    def payout(self) -> Optional[float]:
        """Payout per YES contract in cents. None for voided markets."""
        if self.outcome == "voided":
            return None
        return 100.0 if self.outcome == "yes" else 0.0

    @model_validator(mode="after")
    def _validate(self) -> "Settlement":
        object.__setattr__(
            self, "settled_at", _require_utc(self.settled_at, "settled_at")
        )
        return self


class StrategySignal(BaseModel):
    """A tradeable opportunity emitted by a strategy."""

    timestamp: datetime = Field(..., description="UTC time the signal was generated")
    strategy: str = Field(..., description="Strategy that produced the signal")
    contract_id: str = Field(..., description="Kalshi contract identifier")
    action: Literal["buy_yes", "sell_yes", "buy_no", "sell_no", "close"] = Field(
        ..., description="Recommended action"
    )
    market_price: float = Field(..., ge=0, le=100, description="Price at signal time")
    fair_value: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Model fair value; None for model-free structural signals",
    )
    fair_value_stderr: Optional[float] = Field(
        None,
        ge=0,
        description=(
            "Standard error of the fair value estimate. Required to size on a "
            "model-dependent signal: trading a point estimate ignores estimation "
            "risk and systematically over-bets."
        ),
    )
    gross_edge_cents: float = Field(
        ..., description="Edge in cents before fees and spread"
    )
    net_edge_cents: float = Field(
        ..., description="Edge in cents after modelled fees and spread crossing"
    )
    max_size: float = Field(
        ..., ge=0, description="Contracts available at the assumed price or better"
    )
    metadata: dict = Field(default_factory=dict, description="Strategy-specific detail")

    model_config = FROZEN

    @model_validator(mode="after")
    def _validate(self) -> "StrategySignal":
        object.__setattr__(self, "timestamp", _require_utc(self.timestamp, "timestamp"))
        if self.net_edge_cents > self.gross_edge_cents:
            raise ValueError("net edge cannot exceed gross edge")
        return self


# =============================================================================
# Social and news
# =============================================================================


class SocialPost(BaseModel):
    """A post or comment mentioning one or more tickers."""

    timestamp: datetime = Field(..., description="UTC creation time")
    platform: Literal["reddit", "stocktwits"] = Field(..., description="Source platform")
    post_id: str = Field(..., description="Platform-unique identifier")
    community: str = Field(..., description="Subreddit or stream name")
    author: str = Field(..., description="Author handle")
    title: Optional[str] = Field(None, description="Title, where the platform has one")
    text: str = Field(..., description="Body text")
    tickers_mentioned: list[str] = Field(
        default_factory=list, description="Extracted ticker symbols"
    )
    engagement: int = Field(..., ge=0, description="Score, upvotes or equivalent")
    url: Optional[str] = Field(None, description="Permalink")

    model_config = FROZEN

    @model_validator(mode="after")
    def _validate(self) -> "SocialPost":
        object.__setattr__(self, "timestamp", _require_utc(self.timestamp, "timestamp"))
        return self


class NewsEvent(BaseModel):
    """A scheduled or unscheduled news event.

    ``scheduled`` matters for identification: a scheduled release has a known
    announcement time, so the arrival of information is exogenous in a way that
    an unscheduled headline is not.
    """

    timestamp: datetime = Field(..., description="UTC event time")
    event_type: Literal["earnings", "fed_announcement", "macro_release", "news"] = Field(
        ..., description="Event category"
    )
    ticker: Optional[str] = Field(None, description="Associated ticker, if any")
    title: str = Field(..., description="Headline")
    source: str = Field(..., description="Origin of the record")
    scheduled: bool = Field(
        ..., description="Whether the announcement time was known in advance"
    )
    surprise: Optional[float] = Field(
        None,
        description=(
            "Standardised surprise (actual minus consensus, scaled). Optional, "
            "but without it the event study cannot separate the size of the news "
            "from the speed of its diffusion."
        ),
    )
    url: Optional[str] = Field(None, description="Link to source")

    model_config = FROZEN

    @model_validator(mode="after")
    def _validate(self) -> "NewsEvent":
        object.__setattr__(self, "timestamp", _require_utc(self.timestamp, "timestamp"))
        return self


# =============================================================================
# Market data
# =============================================================================


class MarketData(BaseModel):
    """An OHLCV bar with optional top-of-book and implied volatility."""

    timestamp: datetime = Field(..., description="UTC bar close time")
    ticker: str = Field(..., description="Ticker symbol")
    interval: Literal["1min", "5min", "15min", "1h", "1d"] = Field(
        ..., description="Bar interval"
    )
    open: float = Field(..., gt=0)
    high: float = Field(..., gt=0)
    low: float = Field(..., gt=0)
    close: float = Field(..., gt=0)
    volume: int = Field(..., ge=0)
    bid: Optional[float] = Field(None, gt=0, description="Best bid at bar close")
    ask: Optional[float] = Field(None, gt=0, description="Best ask at bar close")
    implied_vol: Optional[float] = Field(
        None, ge=0, description="ATM implied volatility, annualised"
    )

    model_config = FROZEN

    @model_validator(mode="after")
    def _validate_ohlc(self) -> "MarketData":
        # A field_validator cannot do this: when 'high' is validated, 'low' has
        # not been populated yet, so the cross-field check silently never fires.
        # It has to be a model_validator running after all fields are set.
        object.__setattr__(self, "timestamp", _require_utc(self.timestamp, "timestamp"))

        if self.high < self.low:
            raise ValueError(f"high {self.high} < low {self.low}")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"open {self.open} outside [{self.low}, {self.high}]")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"close {self.close} outside [{self.low}, {self.high}]")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError(f"crossed quote: bid {self.bid} > ask {self.ask}")
        return self


class OptionsData(BaseModel):
    """A single option quote."""

    timestamp: datetime = Field(..., description="UTC quote time")
    ticker: str = Field(..., description="Underlying ticker")
    expiration: datetime = Field(..., description="UTC expiration")
    strike: float = Field(..., gt=0)
    option_type: Literal["call", "put"] = Field(...)
    bid: float = Field(..., ge=0)
    ask: float = Field(..., ge=0)
    implied_vol: float = Field(..., ge=0, description="Annualised implied volatility")
    open_interest: int = Field(..., ge=0)

    model_config = FROZEN

    @model_validator(mode="after")
    def _validate(self) -> "OptionsData":
        object.__setattr__(self, "timestamp", _require_utc(self.timestamp, "timestamp"))
        object.__setattr__(
            self, "expiration", _require_utc(self.expiration, "expiration")
        )
        if self.bid > self.ask:
            raise ValueError(f"crossed quote: bid {self.bid} > ask {self.ask}")
        if self.expiration < self.timestamp:
            raise ValueError("expiration precedes quote timestamp")
        return self


# =============================================================================
# Hawkes process
# =============================================================================


class HawkesEvent(BaseModel):
    """One observed event arrival in a point-process sample."""

    timestamp: datetime = Field(..., description="UTC arrival time")
    event_type: Literal["post", "trade", "news"] = Field(..., description="Event class")
    ticker: str = Field(..., description="Associated ticker")
    mark: float = Field(
        1.0,
        gt=0,
        description=(
            "Event magnitude, e.g. engagement or size. The baseline unmarked "
            "model ignores this; it is only used by the marked extension, where "
            "excitation scales with the mark."
        ),
    )

    model_config = FROZEN

    @model_validator(mode="after")
    def _validate(self) -> "HawkesEvent":
        object.__setattr__(self, "timestamp", _require_utc(self.timestamp, "timestamp"))
        return self


class SimulatedHawkesEvent(HawkesEvent):
    """An event produced by simulation, where the branching structure is known.

    Parentage is latent in real data and cannot be recovered from observed
    arrivals. It exists here only so that simulation output can be used to
    validate the estimator against a known ground truth.
    """

    parent_index: Optional[int] = Field(
        None, description="Index of the triggering event; None for immigrants"
    )

    model_config = FROZEN


class HawkesParameters(BaseModel):
    """Fitted parameters of a univariate Hawkes process with exponential kernel.

    The conditional intensity is

        lambda(t) = mu + sum_{t_i < t} alpha * exp(-beta * (t - t_i))

    where

        mu    baseline intensity, events per unit time
        alpha excitation amplitude, the jump in intensity caused by one event
        beta  exponential decay rate of that excitation

    The branching ratio is the integral of the kernel,

        n = int_0^inf alpha * exp(-beta * s) ds = alpha / beta

    and is the expected number of direct offspring per event. The process is
    stationary if and only if n < 1. Note that mu does not enter the branching
    ratio at all: the baseline controls how many immigrant events arrive, not
    how strongly each event excites the next.

    Branching ratio and half-life are computed from alpha and beta rather than
    stored, so they cannot disagree with the fitted parameters.
    """

    mu: float = Field(..., gt=0, description="Baseline intensity, events per time_unit")
    alpha: float = Field(..., gt=0, description="Excitation amplitude, per time_unit")
    beta: float = Field(..., gt=0, description="Kernel decay rate, per time_unit")
    time_unit: Literal["second", "minute", "hour", "day"] = Field(
        ..., description="Unit that mu, alpha and beta are expressed in"
    )
    log_likelihood: float = Field(..., description="Maximised log-likelihood")
    n_events: int = Field(..., gt=0, description="Events used in the fit")
    observation_window: float = Field(
        ..., gt=0, description="Length of the observation window in time_unit"
    )
    converged: bool = Field(..., description="Whether the optimiser reported success")
    std_errors: dict[str, float] = Field(
        default_factory=dict,
        description="Asymptotic standard errors keyed by parameter name",
    )

    model_config = FROZEN

    @property
    def branching_ratio(self) -> float:
        """Expected direct offspring per event, ``alpha / beta``."""
        return self.alpha / self.beta

    @property
    def is_stationary(self) -> bool:
        """A branching ratio at or above one implies an explosive process."""
        return self.branching_ratio < 1.0

    @property
    def excitation_half_life(self) -> float:
        """Time for excitation to decay by half, ``ln(2) / beta``, in time_unit."""
        return math.log(2.0) / self.beta

    @property
    def expected_cluster_size(self) -> Optional[float]:
        """Mean total events triggered by one immigrant, ``1 / (1 - n)``.

        Undefined for a non-stationary fit.
        """
        if not self.is_stationary:
            return None
        return 1.0 / (1.0 - self.branching_ratio)

    # Deliberately not rejected: a non-stationary fit is a diagnostic result,
    # not a validation failure. Suppressing it would hide the most informative
    # thing the fit can tell you about a badly specified model or a bad window.


class ResidualDiagnostics(BaseModel):
    """Goodness-of-fit result for a fitted point process.

    Under the time-rescaling theorem, if the fitted intensity is correct then
    the compensator-transformed inter-arrival times are i.i.d. unit-rate
    exponential. Failing to report this is the usual tell that a Hawkes fit was
    never validated.
    """

    test_name: str = Field(..., description="Name of the test performed")
    statistic: float = Field(..., description="Test statistic")
    p_value: float = Field(..., ge=0, le=1, description="P-value")
    n_residuals: int = Field(..., gt=0, description="Number of rescaled residuals")
    alpha_level: float = Field(0.05, gt=0, lt=1, description="Significance level used")
    notes: str = Field("", description="Interpretation and caveats")

    model_config = FROZEN

    @property
    def rejects_null(self) -> bool:
        """True when the fit is rejected at ``alpha_level``.

        Not rejecting is weak evidence, not proof of a correct model: an
        underpowered test fails to reject almost anything.
        """
        return self.p_value < self.alpha_level


class AbnormalReturn(BaseModel):
    """Abnormal return over an event window."""

    event_time: datetime = Field(..., description="UTC event time")
    ticker: str = Field(..., description="Ticker symbol")
    window_start_min: int = Field(..., description="Window start, minutes relative to event")
    window_end_min: int = Field(..., description="Window end, minutes relative to event")
    stock_return: float = Field(..., description="Realised return over the window")
    expected_return: float = Field(
        ..., description="Return predicted by the benchmark model"
    )
    abnormal_return: float = Field(..., description="Realised minus expected")
    benchmark: str = Field(
        ..., description="Benchmark model used, e.g. 'market_model', 'ff3'"
    )

    model_config = FROZEN

    @model_validator(mode="after")
    def _validate(self) -> "AbnormalReturn":
        object.__setattr__(
            self, "event_time", _require_utc(self.event_time, "event_time")
        )
        if self.window_end_min <= self.window_start_min:
            raise ValueError("window_end_min must exceed window_start_min")
        return self


# =============================================================================
# Backtesting and execution
# =============================================================================


class BacktestOrder(BaseModel):
    """An order submitted during a backtest, with its realised fill."""

    order_id: str = Field(..., description="Unique identifier")
    timestamp: datetime = Field(..., description="UTC submission time")
    contract_id: str = Field(..., description="Kalshi contract identifier")
    side: Literal["buy", "sell"] = Field(..., description="Side, in YES terms")
    quantity: float = Field(..., gt=0, description="Contracts requested")
    limit_price: float = Field(..., ge=0, le=100, description="Limit price in cents")
    filled_quantity: float = Field(..., ge=0, description="Contracts filled")
    fill_price: Optional[float] = Field(
        None, ge=0, le=100, description="Average fill price; None if unfilled"
    )
    fee_cents: float = Field(..., ge=0, description="Total fee paid, in cents")
    status: Literal["filled", "partial", "unfilled", "cancelled"] = Field(...)

    model_config = FROZEN

    @model_validator(mode="after")
    def _validate(self) -> "BacktestOrder":
        object.__setattr__(self, "timestamp", _require_utc(self.timestamp, "timestamp"))
        if self.filled_quantity > self.quantity:
            raise ValueError("filled_quantity exceeds requested quantity")
        if self.filled_quantity > 0 and self.fill_price is None:
            raise ValueError("a partially or fully filled order needs a fill_price")
        if self.filled_quantity == 0 and self.status in ("filled", "partial"):
            raise ValueError(f"status {self.status!r} inconsistent with zero fill")
        return self


class Position(BaseModel):
    """Net position in one contract."""

    contract_id: str = Field(..., description="Kalshi contract identifier")
    quantity: float = Field(..., description="Net contracts; negative is short YES")
    avg_entry_price: float = Field(..., ge=0, le=100, description="Average entry, cents")
    mark_price: float = Field(..., ge=0, le=100, description="Current mark, cents")
    realised_pnl_cents: float = Field(..., description="Realised P&L in cents")

    model_config = FROZEN

    @property
    def unrealised_pnl_cents(self) -> float:
        return self.quantity * (self.mark_price - self.avg_entry_price)


class PortfolioSnapshot(BaseModel):
    """Portfolio state at one instant during a backtest or paper-trading run."""

    timestamp: datetime = Field(..., description="UTC time")
    cash_cents: float = Field(..., description="Cash balance in cents")
    positions: dict[str, Position] = Field(
        default_factory=dict, description="Contract identifier to position"
    )

    model_config = FROZEN

    @model_validator(mode="after")
    def _validate(self) -> "PortfolioSnapshot":
        object.__setattr__(self, "timestamp", _require_utc(self.timestamp, "timestamp"))
        return self

    @property
    def total_equity_cents(self) -> float:
        return self.cash_cents + sum(
            p.quantity * p.mark_price for p in self.positions.values()
        )

    # Sharpe is deliberately absent. It is a property of a return series, not of
    # a single snapshot, and storing it per-snapshot invites reporting a number
    # computed over an accidental window.


# =============================================================================
# Forecast calibration
# =============================================================================


class CalibrationResult(BaseModel):
    """Out-of-sample calibration of a probabilistic forecaster.

    Calibration is the property that matters for a fair-value model: a
    forecaster that says 70% and is right 70% of the time is well calibrated
    even though it is "wrong" on 30% of individual contracts. Accuracy alone
    does not capture this, which is why it is not reported on its own.

    The Brier score decomposes as

        BS = reliability - resolution + uncertainty

    where reliability measures calibration error (lower is better), resolution
    measures how far forecasts move away from the base rate (higher is better),
    and uncertainty is a property of the outcomes rather than the forecaster.
    """

    name: str = Field(..., description="Forecaster being evaluated")
    test_start: datetime = Field(..., description="UTC start of the evaluation window")
    test_end: datetime = Field(..., description="UTC end of the evaluation window")
    n_forecasts: int = Field(..., gt=0, description="Number of scored forecasts")
    base_rate: float = Field(..., ge=0, le=1, description="Realised outcome frequency")
    brier_score: float = Field(..., ge=0, le=1, description="Mean squared forecast error")
    log_loss: float = Field(..., ge=0, description="Mean negative log-likelihood")
    reliability: Optional[float] = Field(
        None, ge=0, description="Calibration component of the Brier decomposition"
    )
    resolution: Optional[float] = Field(
        None, ge=0, description="Resolution component of the Brier decomposition"
    )
    bins: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="Reliability-diagram bins: forecast bin to count and observed rate",
    )

    model_config = FROZEN

    @model_validator(mode="after")
    def _validate(self) -> "CalibrationResult":
        object.__setattr__(
            self, "test_start", _require_utc(self.test_start, "test_start")
        )
        object.__setattr__(self, "test_end", _require_utc(self.test_end, "test_end"))
        if self.test_end <= self.test_start:
            raise ValueError("test_end must be after test_start")
        return self

    @property
    def uncertainty(self) -> float:
        """Irreducible component of the Brier score, ``p(1 - p)`` at the base rate."""
        return self.base_rate * (1.0 - self.base_rate)

    @property
    def skill_vs_base_rate(self) -> float:
        """Brier skill score against always forecasting the base rate.

        Positive means the forecaster beats the climatological baseline. A
        fair-value model that cannot clear zero here has no business sizing a
        position, however good its accuracy looks.
        """
        if self.uncertainty == 0:
            return 0.0
        return 1.0 - (self.brier_score / self.uncertainty)
