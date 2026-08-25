#!/usr/bin/env python3
"""Collect Kalshi order books continuously.

This must run somewhere that stays up. Order books cannot be backfilled, so a
process that dies with the laptop lid loses days that never come back.

    # what would be collected, and why everything else was dropped
    python scripts/collect_kalshi.py --discover

    # the default universe: exhaustive families with a tradeable worst leg
    python scripts/collect_kalshi.py --auto --min-volume 100 --max-markets 150 --out ./data

    # one series family
    python scripts/collect_kalshi.py --series KXFEDDECISION --interval 15 --out ./data

    # against the demo environment (synthetic books; for plumbing only)
    python scripts/collect_kalshi.py --discover --demo
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from quant.common.api.kalshi import KalshiAuthError, KalshiClient  # noqa: E402
from quant.ingest.discovery import discover, tickers_of  # noqa: E402
from quant.ingest.kalshi_collector import (  # noqa: E402
    KalshiCollector,
    install_signal_handlers,
)


def load_pinned_tickers(path: Path) -> set[str]:
    """Read a newline-delimited ticker list, ignoring blanks and ``#`` comments."""
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Generate one from a previous run's "
            f"data/universe.txt, or omit --tickers-file to select afresh."
        )
    tickers = {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if not tickers:
        raise SystemExit(f"{path} contains no tickers")
    return tickers


def save_universe(path: Path, tickers: list[str]) -> None:
    """Record the universe actually collected, so a restart can reproduce it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(
        f"# Kalshi collection universe, resolved {stamp}\n"
        f"# {len(tickers)} tickers. Re-pin with --tickers-file to keep the panel\n"
        f"# balanced across restarts.\n" + "\n".join(sorted(tickers)) + "\n",
        encoding="utf-8",
    )
    logging.info("universe written to %s (%d tickers)", path, len(tickers))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", type=Path, default=Path("./data"), help="output directory")
    p.add_argument("--interval", type=float, default=30.0, help="seconds between cycles")
    p.add_argument("--depth", type=int, default=0, help="book depth; 0 means all levels")

    sel = p.add_argument_group("selection")
    sel.add_argument("--auto", action="store_true", help="collect the selected universe")
    sel.add_argument("--discover", action="store_true", help="show the selection and exit")
    sel.add_argument("--event", action="append", default=[], help="event ticker; repeatable")
    sel.add_argument("--series", action="append", default=[], help="series ticker or event prefix; repeatable")
    sel.add_argument(
        "--min-volume",
        type=float,
        default=0.0,
        help="minimum volume on the WORST leg of a family (a basket needs every leg tradeable)",
    )
    sel.add_argument("--min-liquidity", type=float, default=0.0, help="same, for liquidity")
    sel.add_argument(
        "--min-ask-size",
        type=float,
        default=0.0,
        help=(
            "minimum contracts resting at the ask on the worst leg — how much of "
            "the basket can actually be lifted right now, as opposed to how much "
            "has traded historically"
        ),
    )
    sel.add_argument(
        "--min-days-to-close",
        type=float,
        default=0.0,
        help=(
            "drop families whose SOONEST leg settles within this many days. "
            "Selecting on volume alone fills the universe with same-day sports, "
            "because that is where Kalshi's volume is. A universe pinned on "
            "2026-08-24 was already 25%% settled contracts, and only a third of "
            "it would have survived a six-week window. Use 45 or more for a "
            "panel intended to run for the length of the project; leave at 0 "
            "for a one-off cross-sectional arbitrage scan."
        ),
    )
    sel.add_argument(
        "--max-legs",
        type=int,
        default=10,
        help=(
            "drop families with more legs than this (0 for no limit). The taker "
            "fee rounds up to a whole cent per leg against a fixed 100c payout, "
            "so a 50-leg basket owes 50c in fees and cannot clear them at any "
            "price. Big fields are ruled out by arithmetic, not by pricing."
        ),
    )
    sel.add_argument(
        "--max-markets",
        type=int,
        default=200,
        help="cap on total markets, keeping the highest-volume families (0 for no cap)",
    )
    sel.add_argument(
        "--tickers-file",
        type=Path,
        default=None,
        help=(
            "pin the universe to a newline-delimited ticker list, bypassing the "
            "volume, liquidity and status filters. Discovery still runs, for the "
            "family metadata, but only these tickers are collected. Use this to "
            "keep a BALANCED PANEL across restarts: without it the universe is "
            "re-selected from live volume on every start, so each restart "
            "silently swaps part of the panel and no series spans the full "
            "history. A coverage audit of the first 39 hours found 298 tickers "
            "across three restarts and not one that covered the whole window."
        ),
    )
    sel.add_argument(
        "--save-universe",
        type=Path,
        default=Path("./data/universe.txt"),
        help=(
            "write the resolved ticker list here on every start, so the panel "
            "that was actually collected is always recoverable and re-pinnable"
        ),
    )
    sel.add_argument(
        "--status",
        action="append",
        default=[],
        choices=["short_arb", "long_watch", "sandwich"],
        help=(
            "which classifications to keep; repeatable. Default is all three. "
            "short_arb: bids sum above 100c, sell the basket (needs only mutual "
            "exclusivity). long_watch: asks sum below 100c, either an arb or a "
            "leaky family. sandwich: the no-arbitrage state, collected because a "
            "dislocation would appear here first."
        ),
    )

    run = p.add_argument_group("runtime")
    run.add_argument("--demo", action="store_true", help="use the demo environment")
    run.add_argument("--max-cycles", type=int, default=None, help="stop after N cycles")
    run.add_argument(
        "--rate",
        type=float,
        default=4.0,
        help="max requests per second (8/s drew 429s on 2026-08-23)",
    )
    run.add_argument("--env-file", type=Path, default=Path(".env"), help="dotenv file")
    run.add_argument(
        "--log-file",
        type=Path,
        default=Path("./data/collector.log"),
        help="rotating log file; a silent death is otherwise undiagnosable",
    )
    run.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Log to a file as well as the console. A live run died silently after 90
    # minutes and left nothing behind to diagnose: the console had scrolled, the
    # window was elsewhere, and the only evidence it had ever run was that the
    # data file stopped growing. An unattended process needs a durable record of
    # its own death.
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                args.log_file, maxBytes=10_000_000, backupCount=5, encoding="utf-8"
            )
        )
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )
    if args.log_file:
        logging.info("logging to %s", args.log_file)

    if args.env_file.exists():
        load_dotenv(args.env_file)
    else:
        logging.warning("%s not found; relying on the ambient environment", args.env_file)

    if not (args.auto or args.discover or args.event or args.series):
        logging.error("pass --auto, --discover, --series, or --event")
        return 2

    try:
        client = KalshiClient.from_env(demo=args.demo, rate_per_sec=args.rate)
    except KalshiAuthError as exc:
        logging.error("%s", exc)
        logging.error(
            "Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in %s. The private "
            "key is a PEM file on disk; do not paste its contents anywhere.",
            args.env_file,
        )
        return 2

    if args.demo:
        logging.warning(
            "demo environment: order books here are synthetic and worthless as "
            "research data. Use it to prove the plumbing, not to collect."
        )

    pinned = load_pinned_tickers(args.tickers_file) if args.tickers_file else None

    if pinned:
        # With a pinned universe the filters must be off. They exist to choose a
        # universe; here the universe is already chosen, and re-applying them
        # would drop pinned tickers whose volume has since fallen - reintroducing
        # exactly the panel churn the pin is meant to prevent.
        families, dropped = discover(
            client,
            statuses=None,
            max_legs=None,
            min_volume=0.0,
            min_liquidity=0.0,
            min_ask_size=0.0,
            series=None,
            events=None,
            max_markets=None,
        )
        families = [f for f in families if pinned.intersection(f.market_tickers)]
        # The families are recorded with their FULL leg list, not trimmed to the
        # pinned set. A bucket-sum is a claim about a complete family, so
        # metadata that quietly dropped unpinned legs would make an incomplete
        # basket look exhaustive. Only the poll list is narrowed.
        tickers = [t for t in tickers_of(families) if t in pinned]

        missing = pinned.difference(tickers)
        if missing:
            # Not an error. A pinned market that has settled or delisted simply
            # ends its series, and knowing which ones is part of describing the
            # panel honestly.
            logging.warning(
                "%d pinned ticker(s) are no longer open and will not be "
                "collected: %s%s",
                len(missing),
                ", ".join(sorted(missing)[:8]),
                " ..." if len(missing) > 8 else "",
            )
        logging.info(
            "universe pinned to %s: %d of %d tickers still open",
            args.tickers_file,
            len(tickers),
            len(pinned),
        )
    else:
        families, dropped = discover(
            client,
            statuses=args.status or None,
            max_legs=args.max_legs or None,
            min_volume=args.min_volume,
            min_liquidity=args.min_liquidity,
            min_ask_size=args.min_ask_size,
            min_days_to_close=args.min_days_to_close,
            series=args.series or None,
            events=args.event or None,
            max_markets=args.max_markets or None,
        )
        tickers = tickers_of(families)

    if args.discover:
        for family in families:
            print(family.describe())
        print(f"\n{len(families)} families, {len(tickers)} markets selected")
        by_status: dict[str, int] = {}
        for f in families:
            by_status[f.status] = by_status.get(f.status, 0) + 1
        if by_status:
            print("  " + "  ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
        for f in families:
            if f.status == "short_arb":
                print(f"  ! {f.event_ticker}: {f.reason}, capacity {f.min_bid_size:.0f}")
        if dropped:
            print("\ndropped:")
            for reason, count in sorted(dropped.items(), key=lambda kv: -kv[1])[:15]:
                print(f"  {count:>6}  {reason}")
        if tickers:
            cycle = len(tickers) / max(args.rate, 1e-9)
            verdict = "OK" if cycle < args.interval else "TOO SLOW"
            print(
                f"\none cycle is about {cycle:.0f}s at {args.rate} req/s "
                f"against a {args.interval:.0f}s interval [{verdict}]"
            )
        return 0

    if not tickers:
        logging.error(
            "nothing selected. Loosen --min-volume, widen --series, or pass "
            "--include-non-exhaustive to see what was filtered."
        )
        return 1

    cycle = len(tickers) / max(args.rate, 1e-9)
    if cycle >= args.interval:
        logging.error(
            "one cycle needs about %.0fs at %.1f req/s but the interval is %.0fs. "
            "Lower --max-markets or raise --interval; collecting under this "
            "setting silently produces irregular sampling.",
            cycle,
            args.rate,
            args.interval,
        )
        return 1

    if args.save_universe:
        target = args.save_universe
        if args.tickers_file and target.resolve() == args.tickers_file.resolve():
            # The pin is a record, not a scratch file. Writing the resolved set
            # back over it would silently drop any pinned market that has since
            # settled - removing both the series and the evidence it was ever
            # part of the panel - and would restamp the header on every restart,
            # losing the date the panel was actually fixed.
            target = target.with_name(f"{target.stem}_active{target.suffix}")
            logging.info(
                "not overwriting the pin at %s; writing the resolved set to %s",
                args.tickers_file,
                target,
            )
        save_universe(target, tickers)

    collector = KalshiCollector(
        client=client,
        out_dir=args.out,
        interval_sec=args.interval,
        depth=args.depth,
    )
    install_signal_handlers(collector)
    stats = collector.run(
        tickers,
        events=[
            {
                "event_ticker": f.event_ticker,
                "series_ticker": f.series_ticker,
                "title": f.title,
                "mutually_exclusive": f.mutually_exclusive,
                "market_tickers": f.market_tickers,
                "ask_sum_cents": f.ask_sum_cents,
                "bid_sum_cents": f.bid_sum_cents,
                "status": f.status,
                "reason": f.reason,
            }
            for f in families
        ],
        max_cycles=args.max_cycles,
    )
    print(stats.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
