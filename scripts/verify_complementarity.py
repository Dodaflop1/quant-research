#!/usr/bin/env python3
"""Verify the YES/NO complementarity that every Kalshi price in this repo rests on.

`parse_orderbook` builds a single YES-terms book by converting NO bids into YES
asks with ``ask = 100 - no_bid``. That line is load-bearing: every spread, every
touch price, every fee calculation and every arbitrage figure in Project 1 is
computed from a book built that way. It has been assumed, not checked.

The identity itself is not empirical - a contract that settles at exactly $1
makes "buy NO at q" and "sell YES at 100-q" the same trade by definition. What
IS empirical, and what this script measures, is whether the data supports using
it:

1.  **Are both ladders actually present?** If the NO ladder is often empty, the
    YES ask is undefined and any price derived from it was invented. This is
    the failure that would quietly fabricate every number downstream.
2.  **Does the book ever cross?** ``best_yes_bid + best_no_bid > 100`` means the
    two sides overlap: either the identity is wrong, the snapshot mixes stale
    prices, or there is a genuine riskless trade. All three matter and they look
    identical until you look at the size and the frequency.
3.  **Are the units what the parser thinks they are?** ``_ladder`` multiplies by
    100 on the assumption the payload is in dollars. If a payload is already in
    cents, every price is off by 100x and the sums land near 10,000 instead of
    near 100.
4.  **What keys does the payload actually carry?** The parser reads
    ``orderbook_fp``/``orderbook`` and ``yes_dollars``/``no_dollars`` with
    fallbacks. If production sends an explicit ask ladder, we should be reading
    it rather than deriving one.

Streams the file; stdlib only; safe on a 200 MB jsonl.

Usage:
    python scripts/verify_complementarity.py data/raw/kalshi_orderbook_2026-08-24.jsonl
    python scripts/verify_complementarity.py data/raw/*.jsonl --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

CROSS_TOLERANCE = 0.5  # cents; below this a "crossing" is rounding, not a trade


def ladder(entries: Any, scale: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for entry in entries or []:
        try:
            out.append((float(entry[0]) * scale, float(entry[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def read_book(payload: dict) -> tuple[list, list, str]:
    """Return (yes_bids, no_bids, shape_tag) in cents."""
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    if not isinstance(book, dict):
        return [], [], "not-a-dict"
    if book.get("yes_dollars") is not None or book.get("no_dollars") is not None:
        return (
            ladder(book.get("yes_dollars"), 100.0),
            ladder(book.get("no_dollars"), 100.0),
            "dollars",
        )
    if book.get("yes") is not None or book.get("no") is not None:
        return ladder(book.get("yes"), 1.0), ladder(book.get("no"), 1.0), "cents"
    return [], [], "empty"


def quantiles(values: list[float], qs=(0.01, 0.25, 0.5, 0.75, 0.99)) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    out = {}
    for q in qs:
        idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        out[f"p{int(q * 100)}"] = ordered[idx]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--json", type=Path, default=None, help="write full results here")
    ap.add_argument("--examples", type=int, default=5, help="crossings to print")
    args = ap.parse_args(argv)

    lines = books = unparseable = 0
    shapes: Counter[str] = Counter()
    book_keys: Counter[str] = Counter()
    payload_keys: Counter[str] = Counter()

    both = yes_only = no_only = neither = 0
    sums: list[float] = []
    crossings: list[dict] = []
    per_market_missing_ask: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    one_sided: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    one_sided_price: dict[str, list[float]] = defaultdict(list)
    price_min, price_max = float("inf"), float("-inf")

    for path in args.paths:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                lines += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    unparseable += 1
                    continue
                if record.get("kind") != "orderbook":
                    continue
                books += 1
                payload = record.get("payload") or {}
                payload_keys.update(k for k in payload if isinstance(payload, dict))
                inner = payload.get("orderbook_fp") or payload.get("orderbook") or {}
                if isinstance(inner, dict):
                    book_keys.update(inner.keys())

                yes_bids, no_bids, shape = read_book(payload)
                shapes[shape] += 1
                ticker = record.get("ticker", "?")

                counts = per_market_missing_ask[ticker]
                counts[1] += 1
                if yes_bids and no_bids:
                    both += 1
                elif yes_bids:
                    yes_only += 1
                    counts[0] += 1
                elif no_bids:
                    no_only += 1
                    one_sided[ticker][1] += 1
                    one_sided_price[ticker].append(max(p for p, _ in no_bids))
                else:
                    neither += 1
                    counts[0] += 1
                if yes_bids and not no_bids:
                    one_sided[ticker][0] += 1
                    one_sided_price[ticker].append(max(p for p, _ in yes_bids))

                for price, _ in yes_bids + no_bids:
                    price_min = min(price_min, price)
                    price_max = max(price_max, price)

                if not (yes_bids and no_bids):
                    continue
                best_yes = max(p for p, _ in yes_bids)
                best_no = max(p for p, _ in no_bids)
                total = best_yes + best_no
                sums.append(total)
                if total > 100.0 + CROSS_TOLERANCE:
                    size = min(
                        sum(s for p, s in yes_bids if p == best_yes),
                        sum(s for p, s in no_bids if p == best_no),
                    )
                    crossings.append(
                        {
                            "ticker": ticker,
                            "at": record.get("received_at"),
                            "best_yes_bid": best_yes,
                            "best_no_bid": best_no,
                            "sum": total,
                            "edge_cents": total - 100.0,
                            "contracts_at_touch": size,
                        }
                    )

    if not books:
        print("no orderbook records found", file=sys.stderr)
        return 1

    missing = sorted(
        ((t, bad, tot) for t, (bad, tot) in per_market_missing_ask.items() if bad),
        key=lambda r: -r[1] / r[2],
    )
    crossings.sort(key=lambda c: -c["edge_cents"])

    results = {
        "lines": lines,
        "unparseable": unparseable,
        "orderbook_records": books,
        "payload_shapes": dict(shapes),
        "payload_keys": dict(payload_keys.most_common()),
        "book_keys": dict(book_keys.most_common()),
        "ladders": {
            "both_sides": both,
            "yes_only_ASK_UNDEFINED": yes_only,
            "no_only": no_only,
            "empty": neither,
            "fraction_with_a_usable_ask": (both) / books,
            "fraction_missing_an_ask": (yes_only + neither) / books,
            "fraction_missing_a_bid": (no_only + neither) / books,
        },
        "price_range_cents": [price_min, price_max],
        "bid_sum_quantiles": quantiles(sums),
        "crossings": {
            "count": len(crossings),
            "fraction": len(crossings) / len(sums) if sums else 0.0,
            "tolerance_cents": CROSS_TOLERANCE,
            "worst": crossings[: args.examples],
        },
        "markets_with_undefined_ask": [
            {"ticker": t, "snapshots_without_ask": b, "snapshots": n, "fraction": b / n}
            for t, b, n in missing[:20]
        ],
    }

    print(f"{books:,} order book snapshots from {len(args.paths)} file(s)\n")

    print("Payload shape")
    for k, v in shapes.items():
        print(f"  {k:<12} {v:>10,}")
    print(f"  book keys seen: {', '.join(sorted(book_keys))}")
    if price_min < float("inf"):
        print(f"  price range: {price_min:.2f} to {price_max:.2f} cents", end="")
        print("  OK" if 0 <= price_min and price_max <= 100.01 else "  <-- OUTSIDE [0, 100]")

    print("\n1. Is a YES ask defined?")
    print(f"  both ladders present   {both:>10,}  ({both / books:.2%})")
    print(f"  YES only, no ask       {yes_only:>10,}  ({yes_only / books:.2%})")
    print(f"  NO only                {no_only:>10,}  ({no_only / books:.2%})")
    print(f"  both empty             {neither:>10,}  ({neither / books:.2%})")

    print("\n2. Does the book cross? (best_yes_bid + best_no_bid, cents)")
    q = results["bid_sum_quantiles"]
    if q:
        print("  " + "  ".join(f"{k}={v:.2f}" for k, v in q.items()))
    print(
        f"  crossings > 100 + {CROSS_TOLERANCE}c: {len(crossings):,} "
        f"({results['crossings']['fraction']:.4%})"
    )
    for c in crossings[: args.examples]:
        print(
            f"    {c['ticker']:<28} {c['best_yes_bid']:6.2f} + {c['best_no_bid']:6.2f} "
            f"= {c['sum']:7.2f}  edge {c['edge_cents']:5.2f}c  "
            f"{c['contracts_at_touch']:.0f} contracts"
        )

    if missing:
        print("\n3. Markets where the ask is undefined most often")
        for t, bad, tot in missing[:10]:
            print(f"    {t:<32} {bad:>7,}/{tot:<7,}  {bad / tot:.1%}")

    both_sided_only = {t: v for t, v in one_sided.items() if v[0] and v[1]}
    results["one_sided_markets"] = {
        "tickers_seen_yes_only_AND_no_only": len(both_sided_only),
        "tickers_only_ever_yes_only": sum(1 for v in one_sided.values() if v[0] and not v[1]),
        "tickers_only_ever_no_only": sum(1 for v in one_sided.values() if v[1] and not v[0]),
        "examples": [
            {"ticker": t, "yes_only": v[0], "no_only": v[1]}
            for t, v in sorted(both_sided_only.items(), key=lambda kv: -sum(kv[1]))[:10]
        ],
    }
    def _med(xs):
        return sorted(xs)[len(xs) // 2] if xs else float("nan")

    yes_grp = sorted(((t, v[0]) for t, v in one_sided.items() if v[0] and not v[1]),
                     key=lambda r: -r[1])
    no_grp = sorted(((t, v[1]) for t, v in one_sided.items() if v[1] and not v[0]),
                    key=lambda r: -r[1])
    results["one_sided_markets"]["yes_only_tickers"] = [
        {"ticker": t, "snapshots": c, "median_best_price_on_populated_side": _med(one_sided_price[t])}
        for t, c in yes_grp]
    results["one_sided_markets"]["no_only_tickers"] = [
        {"ticker": t, "snapshots": c, "median_best_price_on_populated_side": _med(one_sided_price[t])}
        for t, c in no_grp]

    print("\n4. One-sided snapshots: is the YES-only / NO-only balance structural?")
    o = results["one_sided_markets"]
    print(f"    tickers showing BOTH one-sided states  {o['tickers_seen_yes_only_AND_no_only']:>6}")
    print(f"    tickers only ever YES-only             {o['tickers_only_ever_yes_only']:>6}")
    print(f"    tickers only ever NO-only              {o['tickers_only_ever_no_only']:>6}")
    print("\n    YES-only (no ask)                    snapshots   median best YES bid")
    for t, c in yes_grp[:12]:
        print(f"      {t:<36} {c:>7,}   {_med(one_sided_price[t]):>6.1f}c")
    print("\n    NO-only (no bid)                     snapshots   median best NO bid")
    for t, c in no_grp[:12]:
        print(f"      {t:<36} {c:>7,}   {_med(one_sided_price[t]):>6.1f}c")

    yes_roots = {t.rsplit("-", 1)[0] for t, _ in yes_grp}
    no_roots = {t.rsplit("-", 1)[0] for t, _ in no_grp}
    shared = yes_roots & no_roots
    print(f"\n    events contributing to BOTH groups: {len(shared)} of "
          f"{len(yes_roots | no_roots)}")
    for r in sorted(shared)[:8]:
        print(f"      {r}")
    results["one_sided_markets"]["shared_event_roots"] = sorted(shared)

    print("\nVERDICT")
    usable = both / books
    if usable > 0.99 and not crossings:
        print("  Complementarity holds. Both ladders present in >99% of snapshots and")
        print("  the book never crosses. ask = 100 - no_bid is safe to build on.")
    else:
        if usable <= 0.99:
            print(f"  Complementarity itself holds: {both:,} two-sided snapshots, "
                  f"{len(crossings)} crossings.")
            print(f"  But {(yes_only + neither) / books:.2%} of snapshots have no ask and "
                  f"{(no_only + neither) / books:.2%} have no bid,")
            print(f"  and {neither / books:.2%} have no book at all. That is a universe "
                  "problem, not a")
            print("  parser problem: check whether those markets were ever quotable.")
        if crossings:
            print(f"  {len(crossings):,} crossed books. Either the snapshot mixes stale")
            print("  prices across the two ladders, or these are real. Check whether the")
            print("  size at the touch could cover the fee before calling it an edge:")
            print("  an N-leg basket owes at least N cents.")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
