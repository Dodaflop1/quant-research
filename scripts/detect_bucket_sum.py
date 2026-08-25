#!/usr/bin/env python3
"""Scan the order book tape for short-side bucket-sum arbitrage.

For an event whose legs are mutually exclusive, at most one leg resolves YES.
Selling one contract of every leg therefore collects `sum(bids)` and can only
ever owe 100c. When `sum(bids) > 100 + fees`, that is a riskless profit.

Why the short side and not the long side
----------------------------------------
The long basket - buying every leg and collecting the 100c the winner pays -
needs the legs to be **exhaustive**, and Kalshi's `mutually_exclusive` flag does
not assert that. If probability escapes to an unlisted outcome the long basket
pays nothing. The short basket needs only mutual exclusivity, which the exchange
does assert, and an escaped outcome makes it *better*: every short expires
worthless and the whole premium is kept.

It is also the direction the data supports. Ask sums measured on this panel run
100.4-109.2c, so the long basket is closed at the touch, while the overround in
a large field - the minimum tick propping up every longshot - is what pushes bid
sums above 100c.

Three ways this scan could lie, all handled explicitly
------------------------------------------------------
1.  **Staleness.** Legs are polled sequentially on a ~60 s cycle. A basket built
    from one leg at t=0 and another at t=55 s is not a basket anyone could have
    traded. `--max-staleness` bounds the spread of observation times and the
    realised spread is reported per opportunity. This is the difference between
    a real signal and an artifact of the collector's scan order.
2.  **Partial baskets.** An event with any leg unquoted is skipped entirely. A
    sum over the legs that happen to have bids is not a bucket sum.
3.  **Capacity.** The edge is per basket; the basket size is the *minimum* size
    resting at the touch across all legs. Fees round up per leg, so the edge per
    contract also depends on size. Both are reported - an edge of 40c on one
    contract is not a strategy.

Usage:
    python scripts/detect_bucket_sum.py \\
        --books data/raw/kalshi_orderbook_2026-08-24.jsonl \\
        --metadata data/raw/kalshi_metadata_2026-08-24.jsonl \\
        --out results/kalshi/bucket_sum.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant.kalshi.fees import bucket_sum_short_edge_cents  # noqa: E402

log = logging.getLogger("bucket_sum")


def parse_ts(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def load_events(path: Path) -> tuple[dict[str, list[str]], dict[str, str], dict[str, str]]:
    """event_ticker -> legs, ticker -> event, event -> title. Last record wins."""
    legs: dict[str, list[str]] = {}
    titles: dict[str, str] = {}
    owner: dict[str, str] = {}
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for ev in rec.get("events") or []:
                if not ev.get("mutually_exclusive"):
                    continue
                tickers = [t for t in (ev.get("market_tickers") or []) if t]
                if len(tickers) < 2:
                    continue
                et = ev.get("event_ticker")
                legs[et] = tickers
                titles[et] = ev.get("title") or ""
                for t in tickers:
                    owner[t] = et
    return legs, titles, owner


def best_bid(payload: dict) -> tuple[float, float] | None:
    """(price_cents, size) of the best YES bid, or None if the side is empty."""
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    if not isinstance(book, dict):
        return None
    raw = book.get("yes_dollars")
    scale = 100.0
    if raw is None:
        raw, scale = book.get("yes"), 1.0
    best_p, best_s = None, 0.0
    for entry in raw or []:
        try:
            price, size = float(entry[0]) * scale, float(entry[1])
        except (TypeError, ValueError, IndexError):
            continue
        if size <= 0:
            continue
        if best_p is None or price > best_p:
            best_p, best_s = price, size
        elif price == best_p:
            best_s += size
    return (best_p, best_s) if best_p is not None else None


def snapshots(path: Path) -> Iterator[tuple[str, float, dict]]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") != "orderbook":
                continue
            ts = parse_ts(rec.get("received_at")) or parse_ts(rec.get("requested_at"))
            if ts is None:
                continue
            yield rec.get("ticker", "?"), ts, rec.get("payload") or {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--books", type=Path, required=True)
    ap.add_argument("--metadata", type=Path, required=True)
    ap.add_argument("--max-staleness", type=float, default=120.0,
                    help="seconds; widest allowed spread of observation times in a basket")
    ap.add_argument("--min-net-edge", type=float, default=0.0,
                    help="cents per basket, after fees, to report")
    ap.add_argument("--max-legs", type=int, default=0,
                    help="skip events with more legs than this (0 = no limit)")
    ap.add_argument("--out", type=Path, default=Path("results/kalshi/bucket_sum.json"))
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    legs, titles, owner = load_events(args.metadata)
    log.info("%d mutually-exclusive events with >=2 legs", len(legs))

    latest: dict[str, tuple[float, float, float]] = {}   # ticker -> (ts, price, size)
    counters = defaultdict(int)
    best_by_event: dict[str, dict] = {}
    # A null result is only credible with the near-misses beside it. Without
    # these, "nothing found" is indistinguishable from a broken detector.
    near_by_event: dict[str, dict] = {}
    price_sums: list[float] = []
    by_legs: dict[int, list[float]] = defaultdict(list)
    # Completeness by field size. The overround argument is about LARGE fields,
    # and a large field is exactly the one least likely to have every leg quoted
    # at the same moment. Without this table a scan can report "no arbitrage"
    # when what it means is "never observed a whole basket".
    seen_by_legs: dict[int, int] = defaultdict(int)
    complete_by_legs: dict[int, int] = defaultdict(int)
    events_by_legs: dict[int, set] = defaultdict(set)

    for ticker, ts, payload in snapshots(args.books):
        counters["snapshots"] += 1
        event = owner.get(ticker)
        bid = best_bid(payload)
        if bid is None:
            latest.pop(ticker, None)
            counters["no_bid"] += 1
            continue
        latest[ticker] = (ts, bid[0], bid[1])
        if event is None:
            counters["ticker_not_in_any_event"] += 1
            continue

        members = legs[event]
        if args.max_legs and len(members) > args.max_legs:
            counters["too_many_legs"] += 1
            continue
        seen_by_legs[len(members)] += 1
        events_by_legs[len(members)].add(event)
        quotes = [latest.get(t) for t in members]
        if any(q is None for q in quotes):
            counters["partial_basket"] += 1
            continue
        complete_by_legs[len(members)] += 1
        stamps = [q[0] for q in quotes]
        staleness = max(stamps) - min(stamps)
        if staleness > args.max_staleness:
            counters["stale_basket"] += 1
            continue

        counters["baskets_evaluated"] += 1
        prices = [q[1] for q in quotes]
        capacity = min(q[2] for q in quotes)
        if capacity < 1:
            counters["no_capacity"] += 1
            continue

        # Fees round up per leg, so the per-basket edge depends on size. Price
        # the basket at the size actually available, not at one contract.
        edge = bucket_sum_short_edge_cents(prices, contracts=capacity)
        price_sums.append(edge["price_sum_cents"])
        by_legs[len(members)].append(edge["price_sum_cents"])
        if edge["gross_edge_cents"] > 0:
            counters["positive_gross"] += 1
            if edge["net_edge_cents"] <= 0:
                counters["killed_by_fees"] += 1

        record = {
            "event_ticker": event,
            "title": titles.get(event, ""),
            "legs": len(members),
            "at": ts,
            "staleness_seconds": staleness,
            "capacity_contracts": capacity,
            **{k: edge[k] for k in ("price_sum_cents", "gross_edge_cents", "fee_cents",
                                    "net_edge_cents", "collateral_cents",
                                    "return_on_collateral")},
            "total_profit_cents": edge["net_edge_cents"] * capacity,
            "breakeven_price_sum_cents": edge["breakeven_price_sum_cents"],
            "shortfall_cents": edge["breakeven_price_sum_cents"] - edge["price_sum_cents"],
        }
        # Closest approach per event, profitable or not.
        near = near_by_event.get(event)
        if near is None or record["net_edge_cents"] > near["net_edge_cents"]:
            near_by_event[event] = record
        if edge["net_edge_cents"] <= args.min_net_edge:
            continue
        counters["opportunities"] += 1
        prior = best_by_event.get(event)
        if prior is None or record["total_profit_cents"] > prior["total_profit_cents"]:
            best_by_event[event] = record

    found = sorted(best_by_event.values(), key=lambda r: -r["total_profit_cents"])

    print(f"\n{counters['snapshots']:,} snapshots, "
          f"{counters['baskets_evaluated']:,} complete baskets evaluated\n")
    print("Why baskets were skipped")
    for key in ("no_bid", "partial_basket", "stale_basket", "no_capacity",
                "too_many_legs", "ticker_not_in_any_event"):
        if counters[key]:
            print(f"  {key:<28} {counters[key]:>10,}")

    print(f"\n{len(found)} events showed a positive net edge at least once")
    if found:
        print(f"\n{'event':<34} {'legs':>4} {'sum':>8} {'gross':>7} {'fee':>7} "
              f"{'net':>7} {'size':>6} {'total':>9} {'ROC':>7} {'stale':>6}")
        for r in found[:args.top]:
            print(f"{r['event_ticker'][:34]:<34} {r['legs']:>4} "
                  f"{r['price_sum_cents']:>7.1f}c {r['gross_edge_cents']:>6.1f}c "
                  f"{r['fee_cents']:>6.1f}c {r['net_edge_cents']:>6.1f}c "
                  f"{r['capacity_contracts']:>6.0f} "
                  f"${r['total_profit_cents'] / 100:>8.2f} "
                  f"{r['return_on_collateral']:>6.2%} {r['staleness_seconds']:>5.0f}s")
        total = sum(r["total_profit_cents"] for r in found) / 100.0
        print(f"\n  Sum of the single best basket per event: ${total:,.2f}")
        print("  NOT a P&L estimate. Each is one snapshot, sizes are order-book")
        print("  depth rather than fills, and nothing here has been executed.")
    else:
        print("\n  No basket cleared its fees. The short direction is closed at the")
        print("  touch on this tape, which is a result and belongs in the write-up.")

    # --- how close did it get? ------------------------------------------------
    def q(xs, p):
        o = sorted(xs)
        return o[min(len(o) - 1, max(0, int(round(p * (len(o) - 1)))))]

    if price_sums:
        print("\nHow close it got - bid sum across all legs, cents "
              "(100 is the payout owed)")
        print("  " + "  ".join(f"p{int(pp*100)}={q(price_sums, pp):.1f}"
                               for pp in (0.5, 0.9, 0.99, 1.0)))
        print(f"  baskets with a bid sum above 100c: "
              f"{counters['positive_gross']:,} of {len(price_sums):,}")
        if counters["positive_gross"]:
            print(f"  of those, killed by fees: {counters['killed_by_fees']:,}")

        print("\n  Bid sum by field size - the overround argument predicts this "
              "rises with legs")
        print(f"    {'legs':>5} {'events':>7} {'attempts':>9} {'complete':>9} "
              f"{'complete%':>10} {'median':>8} {'max':>8}")
        for k in sorted(seen_by_legs):
            v = by_legs.get(k, [])
            frac = complete_by_legs[k] / seen_by_legs[k] if seen_by_legs[k] else 0.0
            med = f"{q(v, 0.5):.1f}c" if v else "-"
            mx = f"{max(v):.1f}c" if v else "-"
            print(f"    {k:>5} {len(events_by_legs[k]):>7,} {seen_by_legs[k]:>9,} "
                  f"{complete_by_legs[k]:>9,} {frac:>9.1%} {med:>8} {mx:>8}")
        big = [k for k in seen_by_legs if k >= 5]
        if big and all(complete_by_legs[k] == 0 for k in big):
            print("\n    NOTE: no field of 5+ legs ever had every leg quoted at once.")
            print("    The overround result was measured on large fields, so this scan")
            print("    has NOT tested it - it has tested 2-leg events, where the bucket")
            print("    sum is the YES/NO complementarity relation restated.")

    nearest = sorted(near_by_event.values(), key=lambda r: -r["net_edge_cents"])[:args.top]
    if nearest:
        print("\n  Closest baskets, by net edge (all negative means none was tradeable)")
        print(f"  {'event':<34} {'legs':>4} {'sum':>8} {'breakeven':>10} "
              f"{'short by':>9} {'net':>8}")
        for r in nearest:
            print(f"  {r['event_ticker'][:34]:<34} {r['legs']:>4} "
                  f"{r['price_sum_cents']:>7.1f}c {r['breakeven_price_sum_cents']:>9.1f}c "
                  f"{r['shortfall_cents']:>8.1f}c {r['net_edge_cents']:>7.1f}c")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "config": {"max_staleness": args.max_staleness,
                   "min_net_edge": args.min_net_edge, "max_legs": args.max_legs},
        "counters": dict(counters),
        "completeness_by_field_size": {
            str(k): {"events": len(events_by_legs[k]), "attempts": seen_by_legs[k],
                     "complete": complete_by_legs[k],
                     "complete_fraction": (complete_by_legs[k] / seen_by_legs[k]
                                           if seen_by_legs[k] else 0.0)}
            for k in sorted(seen_by_legs)},
        "price_sum_quantiles": {
            f"p{int(pp*100)}": (sorted(price_sums)[min(len(price_sums)-1,
                max(0, int(round(pp*(len(price_sums)-1)))))] if price_sums else None)
            for pp in (0.5, 0.9, 0.99, 1.0)},
        "opportunities": found,
        "closest_baskets": sorted(near_by_event.values(),
                                  key=lambda r: -r["net_edge_cents"])[:50],
    }, indent=2, default=float), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
