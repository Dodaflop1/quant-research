#!/usr/bin/env python3
"""Backfill Kalshi trade prints for the diffusion project.

Trades, unlike order books, can be recovered after the fact. This pulls history
rather than waiting for it to accrue.

    # a quick look before committing to a long run
    python scripts/backfill_trades.py --days 2 --dry-run

    # what you almost certainly want: the collected universe, 90 days
    python scripts/backfill_trades.py --days 90 --tickers-file ./data/universe.txt

    # the whole exchange tape - about 2.8 GB PER DAY, so 90 days is ~250 GB
    python scripts/backfill_trades.py --days 90 --out ./data

    # one series, further back
    python scripts/backfill_trades.py --days 365 --ticker KXFEDDECISION-26SEP

    # summarise what has been downloaded so far
    python scripts/backfill_trades.py --report --out ./data

Interrupt it whenever. Completed days are checkpointed and a re-run resumes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from quant.common.api.kalshi import KalshiAuthError, KalshiClient  # noqa: E402
from quant.ingest.trade_backfill import (  # noqa: E402
    TradeBackfill,
    days_between,
    parse_trade_files,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", type=Path, default=Path("./data"), help="output directory")
    p.add_argument("--days", type=int, default=90, help="how many days back to fetch")
    p.add_argument(
        "--end",
        type=str,
        default=None,
        help="last UTC day to fetch, YYYY-MM-DD (default: today)",
    )
    p.add_argument(
        "--ticker",
        action="append",
        default=[],
        help="restrict to a market ticker; repeatable",
    )
    p.add_argument(
        "--tickers-file",
        type=Path,
        default=None,
        help=(
            "restrict to the tickers in this file - normally data/universe.txt, "
            "the same pinned universe the order book collector uses. STRONGLY "
            "recommended: the whole-exchange tape measured 3.56M trades per 9 "
            "hours on 2026-08-24, about 2.8 GB/day, so 90 days is ~250 GB. "
            "Restricted to the collected universe it is a few hundred MB, and "
            "it aligns the trade data with the order books already being "
            "collected for the same markets"
        ),
    )
    p.add_argument(
        "--chunk-days",
        type=int,
        default=None,
        help=(
            "days per fetch window (default: 1 market-wide, 90 with a ticker "
            "list). A day-sized window per ticker mostly returns nothing and "
            "costs a request; one wide window per ticker does the same job in "
            "a fraction of the calls"
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch a single day and report what came back, then exit",
    )
    p.add_argument(
        "--report",
        action="store_true",
        help="parse what is already downloaded and summarise it, without fetching",
    )
    p.add_argument("--rate", type=float, default=4.0, help="max requests per second")
    p.add_argument("--env-file", type=Path, default=Path(".env"), help="dotenv file")
    p.add_argument(
        "--log-file",
        type=Path,
        default=Path("./data/backfill.log"),
        help="rotating log file",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def report(out_dir: Path) -> int:
    """Summarise the downloaded tape: volume, span, and per-market counts."""
    paths = sorted((out_dir / "raw").glob("kalshi_trades_*.jsonl"))
    if not paths:
        print(f"no trade files in {out_dir / 'raw'} yet")
        return 1

    per_market: Counter[str] = Counter()
    per_side: Counter[str] = Counter()
    first = last = None
    total = 0
    for trade in parse_trade_files(paths):
        total += 1
        per_market[trade.contract_id] += 1
        per_side[trade.taker_side] += 1
        if first is None or trade.timestamp < first:
            first = trade.timestamp
        if last is None or trade.timestamp > last:
            last = trade.timestamp

    if not total:
        print("files exist but contain no parseable trades")
        return 1

    span_days = (last - first).total_seconds() / 86400 if first and last else 0.0
    print(f"{total:,} unique trades across {len(per_market):,} markets")
    print(f"  span   {first:%Y-%m-%d %H:%M} to {last:%Y-%m-%d %H:%M} UTC ({span_days:.1f} days)")
    buys, sells = per_side["buy"], per_side["sell"]
    if buys + sells:
        print(
            f"  taker  buy {buys:,} ({100 * buys / (buys + sells):.0f}%)  "
            f"sell {sells:,} ({100 * sells / (buys + sells):.0f}%)  "
            "— YES terms, a NO-side taker counts as a YES sell"
        )
    if span_days > 0:
        # NOT exchange-wide unless no ticker filter was used. An earlier version
        # printed "exchange-wide" unconditionally; on a 150-market pinned
        # universe that understates the exchange by roughly four orders of
        # magnitude, and it is exactly the sort of mislabelled number that ends
        # up in a methodology section unchallenged.
        print(f"  rate   {total / span_days:,.0f} trades/day across these markets")
        busiest = per_market.most_common(1)[0]
        print(
            f"  peak   {busiest[1] / span_days / 24:,.1f} trades/hour on "
            f"{busiest[0]} — the densest series available for a Hawkes fit"
        )
    print("\n  busiest markets (a Hawkes fit needs a few thousand arrivals):")
    for ticker, count in per_market.most_common(15):
        print(f"    {count:>8,}  {ticker}")
    thick = sum(1 for c in per_market.values() if c >= 2000)
    print(f"\n  {thick} markets have 2,000+ trades and are individually fittable")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                args.log_file, maxBytes=10_000_000, backupCount=3, encoding="utf-8"
            )
        )
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )

    if args.report:
        return report(args.out)

    if args.env_file.exists():
        load_dotenv(args.env_file)

    end = (
        date.fromisoformat(args.end)
        if args.end
        else datetime.now(timezone.utc).date()
    )
    start = end - timedelta(days=max(args.days, 1) - 1)

    try:
        client = KalshiClient.from_env(rate_per_sec=args.rate)
    except KalshiAuthError as exc:
        logging.error("%s", exc)
        return 2

    backfill = TradeBackfill(client, out_dir=args.out)

    if args.dry_run:
        # One day, one ticker set, no checkpoint written. The point is to see
        # the shape of a real payload before starting a run measured in hours.
        day = next(days_between(start, end))
        ticker = args.ticker[0] if args.ticker else None
        count = backfill._fetch_day(ticker, day, day)
        backfill.writer.close()
        print(f"\n{day}: {count} trade records over {backfill.stats.pages} pages")
        print("payload written; run without --dry-run to fetch the full range")
        return 0

    tickers = list(args.ticker)
    if args.tickers_file:
        if not args.tickers_file.exists():
            logging.error("%s not found", args.tickers_file)
            return 2
        tickers += [
            line.strip()
            for line in args.tickers_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        logging.info("restricted to %d tickers from %s", len(tickers), args.tickers_file)

    # Wide windows only make sense per ticker; market-wide a single day is
    # already an enormous unit of work.
    chunk = args.chunk_days if args.chunk_days else (90 if tickers else 1)

    stats = backfill.run(start, end, tickers=tickers or None, chunk_days=chunk)
    print(stats.summary())
    print(f"\nnow run: python scripts/backfill_trades.py --report --out {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
