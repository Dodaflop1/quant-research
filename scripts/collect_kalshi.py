#!/usr/bin/env python3
"""Collect Kalshi order books continuously.

This must run somewhere that stays up. Order books cannot be backfilled, so a
process that dies with the laptop lid loses days that never come back.

    # what would be collected, no credentials touched beyond listing events
    python scripts/collect_kalshi.py --discover

    # collect every mutually exclusive open event, 30s cadence
    python scripts/collect_kalshi.py --auto --interval 30 --out ./data

    # collect specific events
    python scripts/collect_kalshi.py --event KXFED-26MAR --interval 15 --out ./data

    # against the demo environment
    python scripts/collect_kalshi.py --auto --demo --max-cycles 2
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from quant.common.api.kalshi import KalshiAuthError, KalshiClient  # noqa: E402
from quant.ingest.kalshi_collector import (  # noqa: E402
    KalshiCollector,
    discover_markets,
    install_signal_handlers,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=Path("./data"), help="output directory")
    p.add_argument("--interval", type=float, default=30.0, help="seconds between cycles")
    p.add_argument("--depth", type=int, default=0, help="book depth; 0 means all levels")
    p.add_argument("--event", action="append", default=[], help="event ticker; repeatable")
    p.add_argument("--auto", action="store_true", help="collect all mutually exclusive open events")
    p.add_argument("--max-events", type=int, default=None, help="cap on events in --auto mode")
    p.add_argument("--discover", action="store_true", help="list what would be collected, then exit")
    p.add_argument("--demo", action="store_true", help="use the demo environment")
    p.add_argument("--max-cycles", type=int, default=None, help="stop after N cycles")
    p.add_argument("--rate", type=float, default=8.0, help="max requests per second")
    p.add_argument("--env-file", type=Path, default=Path(".env"), help="dotenv file to load")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if args.env_file.exists():
        load_dotenv(args.env_file)
    else:
        logging.warning("%s not found; relying on the ambient environment", args.env_file)

    try:
        client = KalshiClient.from_env(demo=args.demo, rate_per_sec=args.rate)
    except KalshiAuthError as exc:
        logging.error("%s", exc)
        logging.error(
            "Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in %s. "
            "The private key is a PEM file on disk; do not paste its contents anywhere.",
            args.env_file,
        )
        return 2

    if args.auto or args.discover:
        events = discover_markets(client, max_events=args.max_events)
    elif args.event:
        wanted = set(args.event)
        events = [e for e in discover_markets(client, mutually_exclusive_only=False)
                  if e["event_ticker"] in wanted]
        missing = wanted - {e["event_ticker"] for e in events}
        if missing:
            logging.warning("no open event found for: %s", ", ".join(sorted(missing)))
    else:
        logging.error("pass --auto, --discover, or at least one --event")
        return 2

    tickers = [t for e in events for t in e["market_tickers"]]

    if args.discover:
        for e in events:
            flag = "ME " if e["mutually_exclusive"] else "   "
            print(f"{flag}{e['event_ticker']:<28} {len(e['market_tickers']):>3} markets  {e['title']}")
        print(f"\n{len(events)} events, {len(tickers)} markets")
        if tickers:
            est = len(tickers) / max(args.rate, 0.001)
            print(f"one cycle is about {est:.0f}s at {args.rate} req/s "
                  f"(interval is {args.interval:.0f}s)")
        return 0

    if not tickers:
        logging.error("nothing to collect")
        return 1

    collector = KalshiCollector(
        client=client,
        out_dir=args.out,
        interval_sec=args.interval,
        depth=args.depth,
    )
    install_signal_handlers(collector)
    stats = collector.run(tickers, events=events, max_cycles=args.max_cycles)
    print(stats.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
