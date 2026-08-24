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


def bucket_sum_long_edge_cents(
    ask_prices_cents: Iterable[float],
    contracts: float = 1.0,
    multiplier: float = 1.0,
) -> dict[str, float]:
    """Buy one of every bucket: profitable when the asks sum below 100c.

    Owning the whole family pays exactly 100c at settlement, so the trade wins
    when ``sum(asks) + fees < 100``.

    **This direction requires the family to be collectively exhaustive.** The
    100c payout depends on some listed bucket resolving YES; if probability can
    escape to an unlisted outcome the basket can pay nothing, and a sub-100c ask
    sum is then correct pricing rather than free money.

    That matters because the exchange does not certify exhaustiveness, and
    prices cannot settle it either: an ask sum below 100c is *either* an
    arbitrage *or* evidence the family leaks. A snapshot cannot tell those
    apart. A time series can — a persistently sub-100c ask sum is structure, a
    transient dip is opportunity.
    """
    asks = list(ask_prices_cents)
    if not asks:
        raise ValueError("empty basket")

    price_sum = sum(asks)
    gross = 100.0 - price_sum
    fees = basket_taker_fee_cents(asks, contracts, multiplier) / max(contracts, 1.0)
    return {
        "direction": "long",
        "price_sum_cents": price_sum,
        "gross_edge_cents": gross,
        "fee_cents": fees,
        "net_edge_cents": gross - fees,
        "breakeven_price_sum_cents": 100.0 - fees,
    }


def bucket_sum_short_edge_cents(
    bid_prices_cents: Iterable[float],
    contracts: float = 1.0,
    multiplier: float = 1.0,
) -> dict[str, float]:
    """Sell one of every bucket: profitable when the bids sum above 100c.

    Selling YES is buying NO at ``100 - price``, so the basket costs
    ``100N - sum(bids)`` up front. Exactly one leg resolves YES, so ``N-1`` NO
    legs pay 100c each and the profit is ``sum(bids) - 100`` before fees.

    **This direction needs only mutual exclusivity, not exhaustiveness** — and
    that asymmetry is the useful part. At most one leg can resolve YES, so the
    most that can ever be owed is 100c. If probability escapes to an unlisted
    outcome, every short expires worthless and the whole premium is kept, which
    only improves the trade.

    So the direction the exchange's own ``mutually_exclusive`` flag is
    sufficient to identify is the short one. It is also the direction the
    overround makes plausible: in a large field the minimum tick props up every
    longshot, which inflates the summed price far above 100c.

    Capital is the binding constraint here rather than edge. A 184-leg field
    ties up on the order of 100N cents per unit basket, so return on collateral
    is reported alongside the raw edge.
    """
    bids = list(bid_prices_cents)
    if not bids:
        raise ValueError("empty basket")

    price_sum = sum(bids)
    gross = price_sum - 100.0
    fees = basket_taker_fee_cents(bids, contracts, multiplier) / max(contracts, 1.0)
    net = gross - fees
    collateral = 100.0 * len(bids) - price_sum
    return {
        "direction": "short",
        "price_sum_cents": price_sum,
        "gross_edge_cents": gross,
        "fee_cents": fees,
        "net_edge_cents": net,
        "breakeven_price_sum_cents": 100.0 + fees,
        "collateral_cents": collateral,
        "return_on_collateral": (net / collateral) if collateral > 0 else 0.0,
    }


# Retained under the original name; the long direction is what it always
# computed, and the name no longer says which direction it means.
bucket_sum_edge_cents = bucket_sum_long_edge_cents


def worst_case_fee_price_cents() -> float:
    """The price at which the fee is maximised, 50c.

    Useful as a sanity bound: no single leg can cost more than the fee at 50c.
    """
    return 50.0
