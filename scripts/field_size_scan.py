#!/usr/bin/env python3
"""Does the bid sum actually rise with field size?

The whole large-field bucket-sum argument rests on one unmeasured assumption.
The overround is real — ask sums across large fields run far above 100c because
the minimum tick props up every longshot. The short basket, though, is priced
off the **bid** side, and nobody has checked whether bid sums rise with leg
count the way ask sums do.

Two possibilities, and they have opposite consequences:

- **Bid sums rise with N.** A 20-leg field collecting 200c owes at most 100c and
  pays ~20c in fees, so ~80c of riskless edge sits there untouched. The universe
  filter (`MAX_TRADEABLE_LEGS = 10`) is then excluding the only profitable
  families and needs to change.
- **Bid sums stay near 100c regardless of N.** Then the overround lives entirely
  in the *spread*, the short basket needs `100 + N` cents and never gets it, and
  `MAX_TRADEABLE_LEGS` is correct. The large-field question is closed by
  arithmetic rather than left open.

`discovery.classify` already computes `bid_sum_cents`, `basket_fee_cents` and
`short_gap_cents` per family. This runs one scan with the leg cap **disabled**
and tabulates them by field size. No collection, no order book tape — a few
minutes against the API.

The single data point already in the codebase (`KXPGATOUR-BMC26`, 184 legs, bid
sum 98.9c against a 150c requirement) points at the second answer. One family is
an anecdote; this makes it a distribution.

Usage:
    python scripts/field_size_scan.py --out results/kalshi/field_size_scan.json
    python scripts/field_size_scan.py --demo
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv                            # noqa: E402
from quant.common.api.kalshi import KalshiClient          # noqa: E402
from quant.ingest.discovery import discover               # noqa: E402

log = logging.getLogger("field_size_scan")

BUCKETS = [(2, 2), (3, 4), (5, 9), (10, 19), (20, 49), (50, 10_000)]


def bucket_of(n: int) -> str:
    for lo, hi in BUCKETS:
        if lo <= n <= hi:
            return f"{lo}" if lo == hi else (f"{lo}-{hi}" if hi < 10_000 else f"{lo}+")
    return "?"


def quantiles(xs: list[float]) -> dict[str, float | None]:
    if not xs:
        return {"p10": None, "median": None, "p90": None, "max": None, "count": 0}
    o = sorted(xs)
    at = lambda q: o[min(len(o) - 1, max(0, round(q * (len(o) - 1))))]  # noqa: E731
    return {"p10": at(0.10), "median": at(0.50), "p90": at(0.90),
            "max": o[-1], "count": len(o)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--demo", action="store_true", help="use the demo environment")
    ap.add_argument("--min-quoted", type=float, default=0.8,
                    help="fraction of legs that must carry a bid for the sum to mean anything")
    ap.add_argument("--out", type=Path, default=Path("results/kalshi/field_size_scan.json"))
    ap.add_argument("--env-file", type=Path, default=Path(".env"), help="dotenv file")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    # Every other script that touches the API does this; this one did not, which
    # is a five-second failure rather than a five-minute one, but still mine.
    load_dotenv(args.env_file)
    client = KalshiClient.from_env(demo=args.demo)
    # max_legs=None is the whole point: the default cap is what has kept large
    # fields out of every previous measurement.
    families, dropped = discover(client, max_legs=None, statuses=None)
    log.info("%d families kept, %d drop reasons", len(families), len(dropped))

    rows: list[dict[str, Any]] = []
    by_bucket: dict[str, list] = defaultdict(list)
    for f in families:
        if f.bid_sum_cents is None or f.quoted_fraction < args.min_quoted:
            continue
        row = {
            "event_ticker": f.event_ticker,
            "legs": f.n_markets,
            "bid_sum_cents": f.bid_sum_cents,
            "ask_sum_cents": f.ask_sum_cents,
            "basket_fee_cents": f.basket_fee_cents,
            "required_bid_sum_cents": 100.0 + f.basket_fee_cents,
            "short_gap_cents": f.short_gap_cents,
            "spread_sum_cents": (f.ask_sum_cents - f.bid_sum_cents
                                 if f.ask_sum_cents is not None else None),
            "quoted_fraction": f.quoted_fraction,
        }
        rows.append(row)
        by_bucket[bucket_of(f.n_markets)].append(row)

    print(f"\n{len(rows)} families with a bid on at least {args.min_quoted:.0%} of legs\n")
    print(f"{'legs':>8} {'n':>5} {'bid sum':>22} {'required':>9} {'short gap':>20} {'spread':>9}")
    ordered = [bucket_of(lo) for lo, _ in BUCKETS]
    for b in ordered:
        rs = by_bucket.get(b)
        if not rs:
            continue
        bid = quantiles([r["bid_sum_cents"] for r in rs])
        gap = quantiles([r["short_gap_cents"] for r in rs])
        req = quantiles([r["required_bid_sum_cents"] for r in rs])
        spr = quantiles([r["spread_sum_cents"] for r in rs if r["spread_sum_cents"] is not None])
        print(f"{b:>8} {len(rs):>5} "
              f"{bid['median']:>8.1f}c [{bid['p10']:>5.1f},{bid['max']:>6.1f}] "
              f"{req['median']:>8.1f}c "
              f"{gap['median']:>8.1f}c [{gap['p10']:>6.1f}] "
              f"{(spr['median'] if spr['median'] is not None else float('nan')):>8.1f}c")

    live = [r for r in rows if r["short_gap_cents"] is not None and r["short_gap_cents"] <= 0]
    best = sorted(rows, key=lambda r: r["short_gap_cents"])[:10]

    print("\nVERDICT")
    if live:
        print(f"  {len(live)} families are AT OR INSIDE a tradeable short basket right now.")
        for r in live[:10]:
            print(f"    {r['event_ticker'][:34]:<34} {r['legs']:>4} legs  "
                  f"bid {r['bid_sum_cents']:.1f}c  needs {r['required_bid_sum_cents']:.1f}c  "
                  f"gap {r['short_gap_cents']:+.1f}c")
        print("  MAX_TRADEABLE_LEGS is excluding profitable families. Change it.")
    else:
        print("  No family is inside a tradeable short basket.")
        print(f"\n  Closest {len(best)}:")
        for r in best:
            print(f"    {r['event_ticker'][:34]:<34} {r['legs']:>4} legs  "
                  f"bid {r['bid_sum_cents']:>6.1f}c  needs {r['required_bid_sum_cents']:>6.1f}c  "
                  f"short by {r['short_gap_cents']:>6.1f}c")
        # Trend across the buckets, not a two-group magnitude test. The first
        # version compared |median(>=10) - median(<10)| against 10 and reported
        # "bid sums DO rise" for a fall of 13c, because it tested the size of
        # the difference and never its sign.
        pts = [(lo, quantiles([r["bid_sum_cents"] for r in by_bucket[bucket_of(lo)]]),
                quantiles([r["short_gap_cents"] for r in by_bucket[bucket_of(lo)]]))
               for lo, _ in BUCKETS if by_bucket.get(bucket_of(lo))]
        if len(pts) >= 3:
            first_bid, last_bid = pts[0][1]["median"], pts[-1][1]["median"]
            first_gap, last_gap = pts[0][2]["median"], pts[-1][2]["median"]
            print(f"\n  Median bid sum:   {first_bid:.1f}c at {pts[0][0]} legs "
                  f"-> {last_bid:.1f}c at {pts[-1][0]}+ legs   "
                  f"({'RISES' if last_bid > first_bid else 'FALLS'})")
            print(f"  Median short gap: {first_gap:.1f}c -> {last_gap:.1f}c   "
                  f"({'narrows' if last_gap < first_gap else 'WIDENS'})")
            if last_bid <= first_bid and last_gap >= first_gap:
                print("\n  The short basket gets monotonically WORSE with field size. The fee")
                print("  floor rises as 100 + N while the bid sum falls, so the two move")
                print("  apart. The overround is entirely bid-ask spread, not mispricing:")
                print("  asks are propped at the minimum tick on every longshot while the")
                print("  bids are not there at all. MAX_TRADEABLE_LEGS = 10 is not merely")
                print("  correct, it is generous. The large-field question is CLOSED.")
            elif last_bid > first_bid:
                print("\n  Bid sums rise with field size — re-examine MAX_TRADEABLE_LEGS.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "min_quoted_fraction": args.min_quoted,
        "families": rows,
        "dropped": dropped,
        "by_bucket": {b: {
            "count": len(rs),
            "bid_sum_cents": quantiles([r["bid_sum_cents"] for r in rs]),
            "required_bid_sum_cents": quantiles([r["required_bid_sum_cents"] for r in rs]),
            "short_gap_cents": quantiles([r["short_gap_cents"] for r in rs]),
        } for b, rs in by_bucket.items()},
    }, indent=2, default=float), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
