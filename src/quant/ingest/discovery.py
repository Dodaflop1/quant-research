"""Selecting which Kalshi markets are worth collecting.

A live `--discover` run on 2026-08-23 returned 5,344 open events and 64,837
markets. Polling all of them takes hours per cycle, so selection is not a
convenience here: without it the collector cannot run at all.

The two trade directions have different requirements, and the asymmetry decides
what can be identified from the data available at selection time.

**Long the basket** — buy one of every leg, collect 100c at settlement — needs
the family to be collectively exhaustive. The exchange does not certify that:
``mutually_exclusive`` says at most one leg resolves YES, not that at least one
does. "Who will the next Pope be?" is exclusive over seven listed candidates,
but an unlisted candidate can win. Titles cannot settle it either — "2027 Pro
Football Champion" lists all 32 teams and is exhaustive, "Who will win the next
presidential election?" lists 30 names and is not, and both read the same to a
regex. Nor can prices: an ask sum below 100c is *either* an arbitrage *or*
evidence the family leaks, and a snapshot cannot distinguish them.

**Short the basket** — sell one of every leg, collect the bids — needs only
mutual exclusivity. At most one leg resolves YES, so at most 100c is ever owed;
if probability escapes to an unlisted outcome every short expires worthless and
the premium is kept. So the direction the exchange's own flag is sufficient to
identify is the short one.

A live production run on 2026-08-23 sharpened this. Every selected family had an
ask sum above 100c — 100.4c to 109.2c — because summing asks means crossing the
spread on every leg. The long direction is closed at the touch essentially
everywhere, which is what a market with functioning makers looks like. Whether
it ever opens transiently is a time-series question, which is the point of
collecting rather than a reason not to.

That run also killed an earlier heuristic. Testing the ask sum against a band
around 100c rejected 293 families at 243c as "implausible". They were not bad
quotes: in a large field the minimum tick props up every longshot, so the summed
ask runs far above 100c by construction. That is the overround, a structural
feature of many-outcome markets — and the reason the short direction is worth
measuring there.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional, Protocol

from quant.kalshi.fees import basket_taker_fee_cents

log = logging.getLogger(__name__)

# Beyond this many legs a bucket-sum basket cannot clear its own fees.
#
# The taker fee rounds UP to a whole cent per leg, so an N-leg basket costs at
# least N cents against a fixed 100c payout. Measured on the live families:
#
#     legs    fee     required bid sum for a short
#        2      4c    104c
#        5     10c    110c
#       20     20c    120c
#       50     50c    150c
#      184    184c    284c  (more than the basket can ever pay)
#
# KXPGATOUR-BMC26 quoted a bid sum of 98.9c against a 150c requirement. That is
# not a near miss; no dislocation closes a 51c gap. The arithmetic, not the
# pricing, is what rules big fields out.
MAX_TRADEABLE_LEGS = 10

# Families whose price sum falls in this band are treated as exhaustive.
# Deliberately wide: spreads on thin legs push the sum around, and the cost of
# wrongly excluding a family (missed opportunity) is lower than the cost of
# wrongly including one (a detector firing on structural non-exhaustiveness).
EXHAUSTIVE_BAND_CENTS = (88.0, 112.0)

# Fraction of a family's legs that must carry a usable quote before the summed
# price means anything at all.
MIN_QUOTED_FRACTION = 0.8


class _EventSource(Protocol):
    def iter_events(self, status: str = ..., with_nested_markets: bool = ...): ...


@dataclass(frozen=True)
class MarketFamily:
    """One event and its markets, with the metrics selection depends on."""

    event_ticker: str
    series_ticker: str
    title: str
    mutually_exclusive: bool
    market_tickers: list[str]

    ask_sum_cents: Optional[float]
    bid_sum_cents: Optional[float]
    quoted_fraction: float
    min_volume: float
    min_liquidity: float
    min_ask_size: float
    min_bid_size: float
    total_volume: float
    basket_fee_cents: float

    status: str
    reason: str

    closes_at: Optional[datetime] = None
    """When the soonest leg stops trading; None if the payload did not say."""

    @property
    def days_to_close(self) -> Optional[float]:
        """Days until the first leg settles.

        This is the family's usable lifetime as a time series. Selecting purely
        on volume fills the universe with same-day sports, because that is where
        Kalshi's volume is - fine for a cross-sectional arbitrage scan, useless
        for a panel that has to run for weeks.
        """
        if self.closes_at is None:
            return None
        return (self.closes_at - datetime.now(timezone.utc)).total_seconds() / 86400.0

    @property
    def n_markets(self) -> int:
        return len(self.market_tickers)

    @property
    def short_gap_cents(self) -> Optional[float]:
        """How far the bid sum is from clearing fees on a short basket.

        Zero or below means the trade is live now. This is the number that
        decides whether a family is worth watching, and it is what the raw
        distance from 100c hides: a 50-leg family sitting 1c from 100c is 50c
        from tradeable.
        """
        if self.bid_sum_cents is None:
            return None
        return (100.0 + self.basket_fee_cents) - self.bid_sum_cents

    @property
    def long_gap_cents(self) -> Optional[float]:
        """How far the ask sum is from clearing fees on a long basket."""
        if self.ask_sum_cents is None:
            return None
        return self.ask_sum_cents - (100.0 - self.basket_fee_cents)

    @property
    def gap_cents(self) -> float:
        """Distance to a tradeable arbitrage, measured on the short side only.

        The long gap is deliberately excluded from ranking. It goes deeply
        negative for exactly the families that leak — a candidate list whose
        asks sum to 62c scores 34c "inside" the long arbitrage while being no
        arbitrage at all — so ranking on it promotes ambiguity to opportunity.
        The short gap is unambiguous, because that direction needs only mutual
        exclusivity. ``long_gap_cents`` stays available as information.
        """
        return self.short_gap_cents if self.short_gap_cents is not None else float("inf")

    @property
    def collectable(self) -> bool:
        """Anything that quotes and is mutually exclusive is worth a time series.

        Including the no-arbitrage majority: a dislocation, if one ever happens,
        happens to a family that looked ordinary in the snapshot.
        """
        return self.status != "rejected"

    def describe(self) -> str:
        def c(v):
            return f"{v:6.1f}" if v is not None else "     -"

        tag = {"short_arb": "SHORT", "long_watch": "LONG?", "sandwich": "  .  "}.get(
            self.status, "     "
        )
        return (
            f"{tag} {self.event_ticker:<26} {self.n_markets:>3}mkt  "
            f"bid={c(self.bid_sum_cents)} ask={c(self.ask_sum_cents)}  "
            f"fee={self.basket_fee_cents:>4.0f}c  gap={self.gap_cents:>6.1f}c  "
            f"{self.title[:34]}"
        )


# Live production payloads (inspected 2026-08-23) suffix every field with its
# unit: `_dollars` for dollar-denominated price strings, `_fp` for fixed-point
# quantities. The bare names in the API documentation do not appear on the wire,
# which is worth remembering — the docs were right about the orderbook shape and
# the base URL, and wrong here. Both spellings are read, preferring the suffixed
# ones, so a payload change in either direction stays parseable.
_ASK_FIELDS_DOLLARS = ("yes_ask_dollars",)
_ASK_FIELDS_LEGACY = ("yes_ask",)
_BID_FIELDS_DOLLARS = ("yes_bid_dollars",)
_BID_FIELDS_LEGACY = ("yes_bid",)

_CLOSE_FIELDS = ("close_time", "expected_expiration_time", "expiration_time")
_VOLUME_FIELDS = ("volume_fp", "volume")
_LIQUIDITY_FIELDS = ("liquidity_dollars", "liquidity")
_ASK_SIZE_FIELDS = ("yes_ask_size_fp", "yes_ask_size")
_BID_SIZE_FIELDS = ("yes_bid_size_fp", "yes_bid_size")


def _num(value) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _first(market: dict, fields: tuple[str, ...]) -> float:
    for key in fields:
        v = _num(market.get(key))
        if v is not None:
            return v
    return 0.0


def _side_price(
    market: dict, dollar_fields: tuple[str, ...], legacy_fields: tuple[str, ...]
) -> tuple[Optional[float], bool]:
    """One side's quote, and whether its unit is known from the field name.

    Both sides are read separately now: the ask decides the long direction and
    the bid decides the short one, so collapsing them to a single "best
    available price" would discard the comparison that matters.
    """
    for key in dollar_fields:
        v = _num(market.get(key))
        if v is not None:
            return v * 100.0, True
    for key in legacy_fields:
        v = _num(market.get(key))
        if v is not None:
            return v, False
    return None, False


def _to_cents(prices: list[tuple[float, bool]]) -> list[float]:
    """Normalise a family's quotes to cents, deciding any unknown unit family-wide.

    Values from a ``_dollars`` field are already converted. Anything read from a
    bare field name has an ambiguous unit, and that ambiguity cannot be resolved
    one value at a time: a lone ``1`` is either 1c or $1.00, and reading a 1c
    tail bucket as a dollar inflates it to 100c and wrecks the family sum.
    Deciding once for the whole family resolves it — a partition quoted in cents
    always has some leg above 1c, because the legs must sum to 100.
    """
    if not prices:
        return []
    known = [v for v, ok in prices if ok]
    unknown = [v for v, ok in prices if not ok]
    if not unknown:
        return known
    scale = 100.0 if max(unknown) <= 1.0 else 1.0
    return known + [v * scale for v in unknown]


def _close_time(market: dict) -> Optional[datetime]:
    """When this market stops trading, if the payload says."""
    for key in _CLOSE_FIELDS:
        raw = market.get(key)
        if not raw:
            continue
        try:
            if isinstance(raw, (int, float)):
                return datetime.fromtimestamp(float(raw), tz=timezone.utc)
            text = str(raw)
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            return (
                parsed.replace(tzinfo=timezone.utc)
                if parsed.tzinfo is None
                else parsed.astimezone(timezone.utc)
            )
        except (ValueError, OSError, OverflowError):
            continue
    return None


def classify(event: dict, markets: list[dict]) -> MarketFamily:
    """Build a :class:`MarketFamily` and decide whether it looks exhaustive."""
    tickers = [m.get("ticker") for m in markets if m.get("ticker")]
    mutually_exclusive = bool(event.get("mutually_exclusive"))

    raw_asks = [_side_price(m, _ASK_FIELDS_DOLLARS, _ASK_FIELDS_LEGACY) for m in markets]
    raw_bids = [_side_price(m, _BID_FIELDS_DOLLARS, _BID_FIELDS_LEGACY) for m in markets]

    asks = _to_cents([(v, ok) for v, ok in raw_asks if v is not None])
    bids = _to_cents([(v, ok) for v, ok in raw_bids if v is not None])

    ask_sum = sum(asks) if asks else None
    bid_sum = sum(bids) if bids else None

    # Quoted coverage is judged on the ask side: a leg with no ask cannot be
    # bought, so the long basket is not even constructible without it.
    quoted_fraction = (len(asks) / len(markets)) if markets else 0.0

    # The SOONEST-closing leg, not the latest. A bucket-sum basket needs every
    # leg live simultaneously, so the family stops being tradeable the moment
    # its first leg settles - and as a time series it stops being complete at
    # the same instant.
    close_times = [t for t in (_close_time(m) for m in markets) if t is not None]
    closes_at = min(close_times) if close_times else None

    volumes = [_first(m, _VOLUME_FIELDS) for m in markets]
    liquidities = [_first(m, _LIQUIDITY_FIELDS) for m in markets]
    ask_sizes = [_first(m, _ASK_SIZE_FIELDS) for m in markets]
    bid_sizes = [_first(m, _BID_SIZE_FIELDS) for m in markets]

    # The minimum across legs, not the total. A basket is only tradeable if
    # EVERY leg is tradeable, so one dead leg makes the family useless however
    # busy the others are.
    min_volume = min(volumes) if volumes else 0.0
    min_liquidity = min(liquidities) if liquidities else 0.0

    # Contracts of the full basket available at the current asks. Volume is
    # history; this is capacity right now, and it is the number the capacity
    # section of the write-up actually needs.
    min_ask_size = min(ask_sizes) if ask_sizes else 0.0
    min_bid_size = min(bid_sizes) if bid_sizes else 0.0

    # What one unit of the basket costs in fees, at the prices actually quoted.
    # Computed from the asks where available so the estimate reflects real leg
    # prices rather than a uniform-partition approximation: the fee is
    # proportional to p(1-p), so a lopsided family is materially cheaper to
    # trade than an evenly-priced one with the same leg count.
    fee = float(basket_taker_fee_cents(asks, 1.0)) if asks else 0.0

    status, reason = _judge(
        mutually_exclusive, len(markets), quoted_fraction, ask_sum, bid_sum
    )

    return MarketFamily(
        event_ticker=event.get("event_ticker") or "",
        series_ticker=event.get("series_ticker") or "",
        title=event.get("title") or "",
        mutually_exclusive=mutually_exclusive,
        market_tickers=tickers,
        ask_sum_cents=ask_sum,
        bid_sum_cents=bid_sum,
        quoted_fraction=quoted_fraction,
        min_volume=min_volume,
        min_liquidity=min_liquidity,
        min_ask_size=min_ask_size,
        min_bid_size=min_bid_size,
        closes_at=closes_at,
        total_volume=sum(volumes),
        basket_fee_cents=fee,
        status=status,
        reason=reason,
    )


def _judge(
    mutually_exclusive: bool,
    n_markets: int,
    quoted_fraction: float,
    ask_sum: Optional[float],
    bid_sum: Optional[float],
) -> tuple[str, str]:
    """Classify a family by where its quotes sit relative to 100c.

    An earlier version tested the ask sum against a band around 100c and called
    anything outside it implausible. That conflated two different things, and a
    live run made the mistake obvious: 293 families were rejected at 243c. Those
    were not bad quotes. They were large fields where the minimum tick props up
    every longshot, so the summed ask runs far above 100c by construction. That
    is the overround, and it is a structural feature of many-outcome markets
    rather than an error.

    The honest classification uses both sides:

    ``short_arb``   bids sum above 100c. Selling one of every leg collects more
                    than the 100c that can ever be owed. Needs only mutual
                    exclusivity, so the exchange's flag is sufficient to
                    identify it.
    ``long_watch``  asks sum below 100c. Either an arbitrage or evidence that
                    the family leaks probability to unlisted outcomes, and a
                    snapshot cannot distinguish them. Collect and let
                    persistence decide.
    ``sandwich``    bids <= 100c <= asks. The no-arbitrage state, which is what
                    a functioning market looks like almost all the time. Worth
                    collecting precisely because a dislocation would show up
                    here first.
    """
    if not mutually_exclusive:
        return "rejected", "not flagged mutually exclusive"
    if n_markets < 2:
        return "rejected", "single market"
    if ask_sum is None and bid_sum is None:
        return "rejected", "no quotes"
    if quoted_fraction < MIN_QUOTED_FRACTION:
        return "rejected", f"only {quoted_fraction:.0%} of legs quoted"

    if bid_sum is not None and bid_sum > 100.0:
        return "short_arb", f"bids sum to {bid_sum:.0f}c; short the basket"
    if ask_sum is not None and ask_sum < 100.0:
        return "long_watch", f"asks sum to {ask_sum:.0f}c; arb or a leaky family"
    return "sandwich", f"bids {bid_sum or 0:.0f}c <= 100c <= asks {ask_sum or 0:.0f}c"


def discover(
    client: _EventSource,
    *,
    statuses: Optional[Iterable[str]] = None,
    max_legs: Optional[int] = MAX_TRADEABLE_LEGS,
    min_volume: float = 0.0,
    min_liquidity: float = 0.0,
    min_ask_size: float = 0.0,
    min_days_to_close: float = 0.0,
    series: Optional[Iterable[str]] = None,
    events: Optional[Iterable[str]] = None,
    max_markets: Optional[int] = None,
) -> tuple[list[MarketFamily], dict[str, int]]:
    """Scan open events and return the families worth collecting.

    Returns the selected families and a tally of why the rest were dropped, so
    a silent filter cannot quietly shrink the universe to nothing.

    ``max_markets`` caps the total market count, keeping the highest-volume
    families. Truncation is recorded in the tally rather than applied silently.

    ``min_days_to_close`` drops families whose soonest leg settles too soon to
    be worth a place in a longitudinal panel. It defaults to 0, which preserves
    the cross-sectional behaviour, but any run intended to produce a time series
    should set it. Selecting on volume alone fills the universe with same-day
    sports - a universe pinned on 2026-08-24 was already 25% settled contracts
    and only a third of it would have survived a six-week collection window.
    """
    series_filter = {s.upper() for s in series} if series else None
    event_filter = {e.upper() for e in events} if events else None
    wanted = set(statuses) if statuses else {"short_arb", "long_watch", "sandwich"}

    selected: list[MarketFamily] = []
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for event in client.iter_events(status="open", with_nested_markets=True):
        markets = event.get("markets") or []
        if not markets:
            drop("no markets")
            continue

        family = classify(event, markets)

        if event_filter is not None and family.event_ticker.upper() not in event_filter:
            drop("event not requested")
            continue
        if series_filter is not None and not _matches_series(family, series_filter):
            drop("series not requested")
            continue
        if family.status not in wanted:
            drop(family.reason if family.status == "rejected" else f"status {family.status}")
            continue
        if max_legs is not None and family.n_markets > max_legs:
            drop(f"more than {max_legs} legs; fees exceed any achievable edge")
            continue
        if family.min_volume < min_volume:
            drop(f"a leg below min volume {min_volume:g}")
            continue
        if family.min_liquidity < min_liquidity:
            drop(f"a leg below min liquidity {min_liquidity:g}")
            continue
        if family.min_ask_size < min_ask_size:
            drop(f"a leg below min ask size {min_ask_size:g}")
            continue
        if min_days_to_close > 0:
            days = family.days_to_close
            if days is None:
                # No close time in the payload. Keeping it would silently
                # readmit exactly what the filter exists to exclude, so an
                # unknown lifetime is treated as too short and counted.
                drop("no close time in payload")
                continue
            if days < min_days_to_close:
                drop(f"closes in under {min_days_to_close:g} days")
                continue

        selected.append(family)

    # Rank by distance to a tradeable arbitrage, not by size. Sorting on total
    # volume favoured 47- and 50-leg fields that can never clear their own fees,
    # and they consumed the market budget: a live run selected three families,
    # two of them structurally dead, and crowded out the small numeric brackets
    # that are the only viable universe. Statistical power here comes from the
    # number of independent families watched, not from legs within one.
    selected.sort(key=lambda f: (f.gap_cents, -f.total_volume))

    if max_markets is not None:
        kept: list[MarketFamily] = []
        running = 0
        for family in selected:
            if running + family.n_markets > max_markets:
                drop("over max-markets cap")
                continue
            kept.append(family)
            running += family.n_markets
        selected = kept

    return selected, dropped


def _matches_series(family: MarketFamily, series_filter: set[str]) -> bool:
    """Match on series ticker or event-ticker prefix.

    Kalshi event tickers are prefixed by their series (``KXFEDDECISION-26SEP``),
    so a prefix match lets a whole family of related events be named at once.
    """
    series = family.series_ticker.upper()
    event = family.event_ticker.upper()
    return any(
        series == s or event == s or event.startswith(s + "-") or series.startswith(s)
        for s in series_filter
    )


def tickers_of(families: Iterable[MarketFamily]) -> list[str]:
    return [t for f in families for t in f.market_tickers]
