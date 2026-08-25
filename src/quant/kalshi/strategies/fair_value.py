"""Turning a modelled probability into a trade, or into no trade.

The structural strategies in this package are model-free: a bucket sum above
100c is an arithmetic fact, and the only question is whether fees eat it. This
one is not. Everything here rests on a probability that came out of a fitted
model, so the interesting work is in refusing to trade — the three filters
below exist because each of them, left out, produces a backtest that makes
money and a live account that does not.

**Fees are charged per order and rounded up, so edge per contract depends on
size.** At one contract the ceiling dominates: a 50c contract owes 2c on a fee
of 1.75c, which is 4% of a 50c position. The same order for 100 contracts owes
175c, or 1.75c each. Sizing and edge are therefore solved together, not in
sequence, and a per-contract edge quoted without a size is meaningless.

**Estimation risk is not the same as edge.** A fair value of 60c against an ask
of 55c is a 5c edge only if the 60 is right. The model reports a standard
error; the trade requires the edge to clear a multiple of it. Trading a point
estimate is the standard way to turn a well-calibrated model into a losing one,
because the trades that pass a naive filter are disproportionately the ones
where the model is furthest wrong.

**Depth is not volume.** The measured Kalshi panel has 368,438 in cumulative
volume against one contract resting at the ask. Size is capped by what the book
actually shows at the price assumed, and a signal that cannot be filled is not
emitted.

What this does not do
---------------------
It sizes each contract independently. The buckets in a temperature family are
mutually exclusive, so simultaneous YES positions in two of them are partially
hedged and simultaneous NO positions are correlated the other way. Correct
joint sizing needs the covariance of the family, which is the next piece of
work; until then `portfolio_cap_contracts` bounds the family as a whole and the
independence assumption is documented rather than hidden.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from quant.common.db.schema import OrderBookSnapshot, StrategySignal
from quant.kalshi.fees import taker_fee_cents

__all__ = ["FairValueConfig", "evaluate", "kelly_fraction"]


@dataclass(frozen=True)
class FairValueConfig:
    """Every number here is a decision, so each carries its reason.

    ``edge_stderrs``
        Required edge as a multiple of the fair value's standard error. 2.0 is
        not a confidence level — the errors are not normal and the se does not
        cover model misspecification — it is a deliberately blunt margin.

    ``min_net_edge_cents``
        A floor in absolute terms, because on a tightly-estimated probability
        the se can shrink below the tick and 2 standard errors stops being a
        meaningful hurdle. One tick.

    ``kelly_fraction``
        Fraction of full Kelly. 0.25 is the conventional quarter-Kelly; full
        Kelly on an estimated probability is not the growth optimum, it is the
        growth optimum for a probability you know exactly.

    ``uncertainty_penalty_stderrs``
        Kelly is computed at the probability moved one standard error against
        the position, not at the point estimate. This is separate from
        ``edge_stderrs``: that one decides whether to trade, this one decides
        how much, and using the point estimate for the second while being
        careful about the first is the common half-measure.
    """

    edge_stderrs: float = 2.0
    min_net_edge_cents: float = 1.0
    kelly_fraction: float = 0.25
    uncertainty_penalty_stderrs: float = 1.0
    bankroll_cents: float = 100_000.0
    max_contracts: float = math.inf
    portfolio_cap_contracts: float = math.inf


def kelly_fraction(probability: float, cost_cents: float) -> float:
    """Full-Kelly fraction of bankroll for a binary contract.

    A contract bought at ``c`` cents pays 100 and costs ``c``, so the odds are
    ``b = (100 - c)/c`` and the Kelly fraction collapses to

        f* = (100p - c) / (100 - c)

    which is the edge in cents divided by the profit if it wins. Zero or
    negative when the contract is not worth its cost. ``cost_cents`` should
    include the fee, since the fee is part of what is risked.
    """
    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"probability must lie in [0, 1]: {probability}")
    if not 0.0 < cost_cents < 100.0:
        # At 0 the position is free and Kelly is unbounded; at 100 it cannot win.
        raise ValueError(f"cost_cents must lie strictly in (0, 100): {cost_cents}")
    return max(0.0, (100.0 * probability - cost_cents) / (100.0 - cost_cents))


def _fee_per_contract(price_cents: float, contracts: float) -> float:
    """Taker fee per contract at this size, in cents.

    The exchange rounds the *order* fee up, so this falls with size and is at
    its worst for a single contract. Sizing has to use the fee at the size it
    is actually proposing.
    """
    if contracts <= 0:
        return math.inf
    return taker_fee_cents(price_cents, contracts) / contracts


def _size_position(
    probability: float,
    price_cents: float,
    stderr: float,
    available: float,
    cfg: FairValueConfig,
) -> tuple[float, float]:
    """Contracts to buy, and the fee per contract at that size.

    Solved as a small fixed point: the fee depends on the size, the size
    depends on the cost, and the cost includes the fee. Two passes are enough —
    the fee per contract is monotone in size and the second pass moves it by
    well under a tick — and the loop is bounded rather than iterated to
    convergence so that a pathological input cannot hang a live loop.
    """
    if available <= 0:
        return (0.0, math.inf)

    penalised = probability - cfg.uncertainty_penalty_stderrs * stderr
    if penalised <= 0.0:
        return (0.0, math.inf)

    contracts = min(available, cfg.max_contracts, cfg.portfolio_cap_contracts)
    fee_pc = _fee_per_contract(price_cents, contracts)

    for _ in range(2):
        cost = price_cents + fee_pc
        if cost >= 100.0:
            return (0.0, fee_pc)
        f_star = kelly_fraction(penalised, cost)
        if f_star <= 0.0:
            return (0.0, fee_pc)
        want = cfg.kelly_fraction * f_star * cfg.bankroll_cents / cost
        contracts = min(want, available, cfg.max_contracts, cfg.portfolio_cap_contracts)
        contracts = math.floor(contracts)
        if contracts <= 0:
            return (0.0, fee_pc)
        fee_pc = _fee_per_contract(price_cents, contracts)

    return (float(contracts), fee_pc)


def evaluate(
    book: OrderBookSnapshot,
    fair_probability: float,
    stderr: float,
    cfg: FairValueConfig = FairValueConfig(),
    now: Optional[datetime] = None,
    strategy: str = "fair_value",
    metadata: Optional[dict] = None,
) -> Optional[StrategySignal]:
    """The best trade in one contract, or ``None`` if there is not one.

    Both directions are considered. Buying YES crosses to the ask; buying NO
    crosses the other way, which on Kalshi means paying ``100 - best_yes_bid``
    for the NO contract. The fee formula is symmetric about 50c, so a NO at
    ``100 - b`` owes the same fee as a YES at ``b`` — but the *edge* is not
    symmetric, because the two sides face different prices across the spread.

    Returns ``None`` when the book is one-sided on the relevant side, when the
    edge does not clear the fee, when it does not clear ``edge_stderrs``
    standard errors, or when the size that survives is under one contract.
    """
    if not 0.0 <= fair_probability <= 1.0:
        raise ValueError(
            f"fair_probability must lie in [0, 1]; got {fair_probability}. "
            "Are these cents rather than odds?"
        )
    if stderr < 0:
        raise ValueError(f"stderr must be non-negative: {stderr}")

    fair_cents = 100.0 * fair_probability
    hurdle = max(cfg.min_net_edge_cents, cfg.edge_stderrs * 100.0 * stderr)
    stamp = now or datetime.now(timezone.utc)

    candidates: list[tuple[float, StrategySignal]] = []

    ask = book.best_yes_ask
    if ask is not None:
        gross = fair_cents - ask
        available = book.depth_up_to("yes_ask", ask)
        size, fee_pc = _size_position(fair_probability, ask, stderr, available, cfg)
        net = gross - fee_pc
        if size >= 1 and net >= hurdle:
            candidates.append((net * size, StrategySignal(
                timestamp=stamp,
                strategy=strategy,
                contract_id=book.contract_id,
                action="buy_yes",
                market_price=ask,
                fair_value=fair_cents,
                fair_value_stderr=100.0 * stderr,
                gross_edge_cents=gross,
                net_edge_cents=net,
                max_size=size,
                metadata={**(metadata or {}), "fee_per_contract_cents": fee_pc,
                          "hurdle_cents": hurdle, "book_depth": available},
            )))

    bid = book.best_yes_bid
    if bid is not None:
        no_price = 100.0 - bid
        gross = (100.0 - fair_cents) - no_price          # == bid - fair_cents
        available = book.depth_up_to("yes_bid", bid)
        size, fee_pc = _size_position(1.0 - fair_probability, no_price, stderr, available, cfg)
        net = gross - fee_pc
        if size >= 1 and net >= hurdle:
            candidates.append((net * size, StrategySignal(
                timestamp=stamp,
                strategy=strategy,
                contract_id=book.contract_id,
                action="buy_no",
                market_price=no_price,
                fair_value=100.0 - fair_cents,
                fair_value_stderr=100.0 * stderr,
                gross_edge_cents=gross,
                net_edge_cents=net,
                max_size=size,
                metadata={**(metadata or {}), "fee_per_contract_cents": fee_pc,
                          "hurdle_cents": hurdle, "book_depth": available,
                          "yes_fair_value_cents": fair_cents},
            )))

    if not candidates:
        return None
    # Rank by total expected edge, not per-contract: a 1c edge on 500 fillable
    # contracts beats a 6c edge on the single contract resting at the touch.
    return max(candidates, key=lambda pair: pair[0])[1]
