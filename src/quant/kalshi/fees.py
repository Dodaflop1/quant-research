"""Kalshi fee model.

Formulas taken from Kalshi's published fee schedule (kalshi.com/docs/
kalshi-fee-schedule.pdf, July 2026 revision, read 2026-08-23):

    taker fee = roundup(M x 0.07   x C x P x (1 - P))
    maker fee = roundup(M x 0.0175 x C x P x (1 - P))

with P the price in dollars, C the contract count and M a per-series
multiplier that defaults to 1 for takers. Certain series carry non-standard
multipliers between 0 and 2.

Two consequences drive strategy design:

* The fee is quadratic in price and peaks at 50c, falling to nearly nothing at
  the tails. Cost is therefore wildly non-uniform across the legs of a basket.
  A detector that ranks opportunities by raw price deviation will over-select
  mid-priced legs, which are precisely the expensive ones.
* A flat basis-point assumption manufactures edge near the tails and destroys
  it in the middle. It is not a conservative simplification in either
  direction.

VERIFY BEFORE TRUSTING: the rounding granularity and the per-series multiplier
table are both taken from the published schedule and have not yet been checked
against a settled trade on the account. Reconcile against a real fill before
any of this is used to size a position.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from typing import Iterable, Literal

TAKER_RATE = Decimal("0.07")
MAKER_RATE = Decimal("0.0175")

_HUNDRED = Decimal(100)


def _d(value: float | int | str | Decimal) -> Decimal:
    """Exact Decimal from a value, going via str so float noise is not inherited."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _fee_cents(
    rate: Decimal,
    price_cents: float,
    contracts: float,
    multiplier: float,
) -> int:
    """Fee in whole cents, rounded up.

    Computed in exact decimal arithmetic rather than binary floating point.
    In float, ``20/100 * 80/100`` evaluates to 0.16000000000000003, so a fee
    that is exactly 11200 cents ceilings to 11201 and the formula stops being
    symmetric about 50c. A one-cent error in the function that decides whether
    an edge survives costs is not an acceptable rounding artefact.
    """
    if not 0 <= price_cents <= 100:
        raise ValueError(f"price_cents out of range: {price_cents}")
    if contracts < 0:
        raise ValueError(f"contracts must be non-negative: {contracts}")

    p = _d(price_cents) / _HUNDRED
    fee_dollars = _d(multiplier) * rate * _d(contracts) * p * (Decimal(1) - p)
    fee_in_cents = fee_dollars * _HUNDRED
    return int(fee_in_cents.to_integral_value(rounding=ROUND_CEILING))


def taker_fee_cents(price_cents: float, contracts: float, multiplier: float = 1.0) -> int:
    """Fee in cents for crossing the spread on ``contracts`` at ``price_cents``."""
    return _fee_cents(TAKER_RATE, price_cents, contracts, multiplier)


def maker_fee_cents(price_cents: float, contracts: float, multiplier: float = 0.0) -> int:
    """Fee in cents for a resting order that fills.

    The multiplier defaults to zero because maker fees apply only on series
    where the schedule specifies them.
    """
    return _fee_cents(MAKER_RATE, price_cents, contracts, multiplier)


def fee_cents(
    price_cents: float,
    contracts: float,
    role: Literal["taker", "maker"] = "taker",
    multiplier: float | None = None,
) -> int:
    if role == "taker":
        return taker_fee_cents(price_cents, contracts, 1.0 if multiplier is None else multiplier)
    return maker_fee_cents(price_cents, contracts, 0.0 if multiplier is None else multiplier)


def basket_taker_fee_cents(
    prices_cents: Iterable[float], contracts: float, multiplier: float = 1.0
) -> int:
    """Total taker fee for buying one unit of every leg in a basket.

    Fees are charged per leg, so a basket of many cheap legs can still cost
    more than its headline deviation suggests.
    """
    return sum(taker_fee_cents(p, contracts, multiplier) for p in prices_cents)


def bucket_sum_edge_cents(
    ask_prices_cents: Iterable[float],
    contracts: float = 1.0,
    multiplier: float = 1.0,
) -> dict[str, float]:
    """Evaluate a bucket-sum arbitrage on an exhaustive, mutually exclusive family.

    Buying one contract of every bucket guarantees exactly 100c at settlement,
    so the trade is profitable when the summed ask prices plus fees fall below
    100c per unit.

    Returns the gross and net edge in cents per unit, and the breakeven price
    sum. ``net_edge_cents`` at or below zero means there is no trade, however
    large the raw deviation looks.

    This is the underpriced direction only. The overpriced direction requires
    shorting the basket, whose cost depends on the bid ladder and on collateral
    treatment, and is not modelled here.
    """
    asks = list(ask_prices_cents)
    if not asks:
        raise ValueError("empty basket")

    price_sum = sum(asks)
    gross = 100.0 - price_sum
    fees = basket_taker_fee_cents(asks, contracts, multiplier) / max(contracts, 1.0)
    return {
        "price_sum_cents": price_sum,
        "gross_edge_cents": gross,
        "fee_cents": fees,
        "net_edge_cents": gross - fees,
        "breakeven_price_sum_cents": 100.0 - fees,
    }


def worst_case_fee_price_cents() -> float:
    """The price at which the fee is maximised, 50c.

    Useful as a sanity bound: no single leg can cost more than the fee at 50c.
    """
    return 50.0
