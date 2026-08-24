#!/usr/bin/env python3
"""Print the real shape of a Kalshi events payload.

Written because the documented market fields (yes_bid, yes_ask, last_price) did
not appear in a live production response: discovery rejected all 4,987
mutually-exclusive families for having no quotes, which is a payload-shape
failure rather than a liquidity one.

Prints field names and a sample market. All of it is public market data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from quant.common.api.kalshi import KalshiClient  # noqa: E402

PRICEY = ("price", "bid", "ask", "yes", "no", "last", "volume", "liquidity", "interest")


def main() -> int:
    demo = "--demo" in sys.argv
    if Path(".env").exists():
        load_dotenv(".env")
    client = KalshiClient.from_env(demo=demo, rate_per_sec=2.0)

    raw = client.get(
        "/events",
        {"limit": 20, "status": "open", "with_nested_markets": "true"},
    )
    body = raw.payload

    print(f"top-level keys: {sorted(body.keys())}")
    events = body.get("events") or []
    print(f"events returned: {len(events)}")
    if not events:
        print("no events; nothing to inspect")
        return 1

    with_markets = [e for e in events if e.get("markets")]
    print(f"events carrying a non-empty 'markets' list: {len(with_markets)}/{len(events)}")

    if not with_markets:
        print("\n!! nested markets are absent entirely")
        print(f"event keys: {sorted(events[0].keys())}")
        print("\nsample event:")
        print(json.dumps(events[0], indent=2)[:1500])
        return 0

    event = with_markets[0]
    print(f"\nevent keys: {sorted(event.keys())}")
    print(f"event_ticker={event.get('event_ticker')!r} "
          f"mutually_exclusive={event.get('mutually_exclusive')!r} "
          f"markets={len(event['markets'])}")

    market = event["markets"][0]
    print(f"\nmarket keys ({len(market)}): {sorted(market.keys())}")

    print("\nprice-ish fields on this market:")
    hits = {k: v for k, v in market.items() if any(p in k.lower() for p in PRICEY)}
    for k, v in sorted(hits.items()):
        print(f"  {k:<28} {v!r}")
    if not hits:
        print("  (none)")

    print("\nfull sample market:")
    print(json.dumps(market, indent=2)[:2000])

    # How widespread is the emptiness across this page?
    total = sum(len(e.get("markets") or []) for e in with_markets)
    for field in ("yes_ask", "yes_bid", "last_price", "volume"):
        present = sum(
            1
            for e in with_markets
            for m in e["markets"]
            if m.get(field) not in (None, 0, "0")
        )
        print(f"{field:<12} non-zero on {present}/{total} markets in this page")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
