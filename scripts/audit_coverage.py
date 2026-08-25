#!/usr/bin/env python3
"""Audit what was actually collected, as opposed to what the log claims.

A collector that reports "0 errors" is telling you it never raised an
exception. It is not telling you the sampling was regular, that every market
was polled every cycle, or that the process was alive the whole time. Those are
different claims and they need to be checked against the files.

This matters twice over:

* **Operationally.** The collector has already died once, silently, after 90
  minutes. A gap is invisible in a log that scrolled away.
* **For the write-up.** Any event-time analysis rests on knowing the sampling
  interval. "60-second snapshots" is a claim about the data that has to be
  substantiated, and the honest version names the gaps rather than implying
  there are none.

Deliberately stdlib-only and streaming: the raw files are hundreds of megabytes
and this has to run wherever the data happens to live.

    python scripts/audit_coverage.py --data ./data
    python scripts/audit_coverage.py --data ./data --gap-threshold 180
    python scripts/audit_coverage.py --data ./data --per-market
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_iso(text: str) -> datetime:
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def human(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def audit(data_dir: Path, gap_threshold: float, per_market: bool) -> int:
    paths = sorted((data_dir / "raw").glob("kalshi_orderbook_*.jsonl"))
    if not paths:
        print(f"no order book files in {data_dir / 'raw'}")
        return 1

    # Cycle times are the collector's own heartbeat: one poll of every market.
    # Per-market times catch the subtler failure where the loop keeps running
    # but individual tickers stop returning.
    cycle_times: list[datetime] = []
    first_seen: dict[str, datetime] = {}
    last_seen: dict[str, datetime] = {}
    market_deltas: list[float] = []
    per_market_gaps: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    error_types: Counter[str] = Counter()
    total = 0
    malformed = 0
    seen_this_second: set[tuple[str, str]] = set()
    duplicates = 0

    print(f"reading {len(paths)} file(s)...")
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue

                kind = record.get("kind")
                ticker = record.get("ticker")
                if kind == "error":
                    errors[ticker] += 1
                    error_types[record.get("error_type", "?")] += 1
                    continue
                if kind != "orderbook" or not ticker:
                    continue

                try:
                    at = parse_iso(record["received_at"])
                except (KeyError, ValueError):
                    malformed += 1
                    continue

                total += 1
                counts[ticker] += 1
                cycle_times.append(at)

                key = (ticker, at.isoformat(timespec="seconds"))
                if key in seen_this_second:
                    duplicates += 1
                seen_this_second.add(key)

                previous = last_seen.get(ticker)
                if previous is None:
                    first_seen[ticker] = at
                else:
                    delta = (at - previous).total_seconds()
                    if delta > gap_threshold:
                        per_market_gaps[ticker].append((previous, delta))
                    else:
                        market_deltas.append(delta)
                last_seen[ticker] = at

    if not total:
        print("files exist but contain no order book records")
        return 1

    cycle_times.sort()
    first, last = cycle_times[0], cycle_times[-1]
    wall = (last - first).total_seconds()

    print(f"\n{'=' * 68}")
    print("COVERAGE AUDIT")
    print(f"{'=' * 68}")
    print(f"  snapshots     {total:,}")
    print(f"  markets       {len(counts):,}")
    print(f"  first         {first:%Y-%m-%d %H:%M:%S} UTC")
    print(f"  last          {last:%Y-%m-%d %H:%M:%S} UTC")
    print(f"  wall clock    {human(wall)}")
    if malformed:
        print(f"  malformed     {malformed:,} lines skipped")
    if duplicates:
        print(f"  duplicates    {duplicates:,} same ticker+second")

    # -- process-level gaps: the whole collector stopped ---------------------
    #
    # Measured on distinct snapshot seconds rather than raw records, because
    # 150 markets land within the same few seconds each cycle and the raw
    # inter-record deltas are almost all zero.
    seconds = sorted({t.replace(microsecond=0) for t in cycle_times})
    outages = [
        (a, (b - a).total_seconds())
        for a, b in zip(seconds, seconds[1:])
        if (b - a).total_seconds() > gap_threshold
    ]

    covered = wall - sum(length for _, length in outages)
    uptime = 100.0 * covered / wall if wall else 0.0

    print(f"\n  {'-' * 64}")
    print(f"  COLLECTOR OUTAGES (nothing written for > {human(gap_threshold)})")
    print(f"  {'-' * 64}")
    if not outages:
        print(f"  none. Continuous coverage across the whole {human(wall)} span.")
    else:
        lost = sum(length for _, length in outages)
        print(f"  {len(outages)} outage(s), {human(lost)} lost, uptime {uptime:.2f}%")
        print()
        for start, length in sorted(outages, key=lambda g: -g[1])[:15]:
            end = start + timedelta(seconds=length)
            print(
                f"    {start:%m-%d %H:%M:%S} -> {end:%m-%d %H:%M:%S} UTC   "
                f"{human(length):>8} dark"
            )
        if len(outages) > 15:
            print(f"    ... and {len(outages) - 15} shorter")

    # -- sampling regularity -------------------------------------------------
    #
    # Measured PER MARKET, which is the only version that means anything. An
    # earlier draft measured the gap between consecutive distinct snapshot
    # seconds across all markets and reported "median 1s" - true, and
    # completely misleading: 150 markets are written across the ~34 seconds of
    # each cycle, so consecutive writes are always about a second apart while
    # any individual market is sampled once a minute. Quoting that number in a
    # methodology section would have claimed one-second resolution for
    # one-minute data.
    if market_deltas:
        market_deltas.sort()
        median = market_deltas[len(market_deltas) // 2]
        p95 = market_deltas[int(len(market_deltas) * 0.95)]
        p99 = market_deltas[int(len(market_deltas) * 0.99)]
        print(f"\n  {'-' * 64}")
        print("  SAMPLING INTERVAL PER MARKET (excluding outages)")
        print(f"  {'-' * 64}")
        print(
            f"    median {median:.0f}s    p95 {p95:.0f}s    "
            f"p99 {p99:.0f}s    max {market_deltas[-1]:.0f}s"
        )
        print(
            "\n    This is the number the write-up can defend: how long an"
            "\n    individual market waits between snapshots. Quote the median as"
            "\n    the sampling rate and the p95 as the honest tail."
        )

    # -- panel balance -------------------------------------------------------
    #
    # A raw completeness percentage cannot distinguish "this market entered the
    # universe late" from "this market stopped responding". Splitting on when
    # each ticker first and last appears separates them, and the distinction
    # decides whether a series is usable: a late entrant is a shorter panel, a
    # mid-series dropout is a hole.
    edge = 2.0 * gap_threshold
    full, late, dropped, brief = [], [], [], []
    for ticker, count in counts.items():
        starts_late = (first_seen[ticker] - first).total_seconds() > edge
        ends_early = (last - last_seen[ticker]).total_seconds() > edge
        if starts_late and ends_early:
            brief.append(ticker)
        elif starts_late:
            late.append(ticker)
        elif ends_early:
            dropped.append(ticker)
        else:
            full.append(ticker)

    print(f"\n  {'-' * 64}")
    print("  PANEL BALANCE")
    print(f"  {'-' * 64}")
    print(f"    {len(full):>4} market(s) span the full {human(wall)} window")
    print(f"    {len(late):>4} entered the universe after collection began")
    print(f"    {len(dropped):>4} stopped before collection ended")
    print(f"    {len(brief):>4} both - present only for a middle slice")

    if len(full) < len(counts) * 0.9:
        print(
            f"\n    Only {len(full)} of {len(counts)} tickers cover the whole span."
            "\n    The universe is re-selected from live volume every time the"
            "\n    collector starts, so each restart swaps part of the panel. That"
            "\n    is fine for cross-sectional snapshots and wrong for anything"
            "\n    that needs a fixed set of series through time. Pin the universe"
            "\n    to a ticker list before the next restart if the time-series"
            "\n    analysis needs a balanced panel."
        )

    for label, group in (("stopped mid-series", dropped), ("middle slice only", brief)):
        if group:
            print(f"\n    {label} ({len(group)}):")
            for ticker in sorted(group, key=lambda t: counts[t])[:8]:
                print(
                    f"      {counts[ticker]:>7,} snaps  "
                    f"{first_seen[ticker]:%m-%d %H:%M} -> "
                    f"{last_seen[ticker]:%m-%d %H:%M}  {ticker}"
                )
            if len(group) > 8:
                print(f"      ... and {len(group) - 8} more")

    if errors:
        print(f"\n  {'-' * 64}")
        print(f"  PER-MARKET ERRORS  ({sum(errors.values()):,} total)")
        print(f"  {'-' * 64}")
        for kind, count in error_types.most_common():
            print(f"    {count:>8,}  {kind}")
        for ticker, count in errors.most_common(8):
            print(f"    {count:>8,}  {ticker}")

    if per_market and per_market_gaps:
        print(f"\n  {'-' * 64}")
        print(f"  PER-MARKET GAPS not explained by an outage")
        print(f"  {'-' * 64}")
        # An outage is explained if the market's gap OVERLAPS it, not if it
        # starts on the same second. A market last polled 30s before the
        # collector stopped has a gap that begins 30s early; exact-second
        # matching called every one of those unexplained and buried the real
        # signal under a list of markets that were behaving perfectly.
        spans = [(start, start + timedelta(seconds=length)) for start, length in outages]

        def explained(at: datetime, length: float) -> bool:
            end = at + timedelta(seconds=length)
            return any(at < o_end and o_start < end for o_start, o_end in spans)

        shown = 0
        for ticker, gaps in sorted(
            per_market_gaps.items(), key=lambda kv: -max(g[1] for g in kv[1])
        ):
            unexplained = [
                (at, length) for at, length in gaps if not explained(at, length)
            ]
            if not unexplained:
                continue
            worst = max(unexplained, key=lambda g: g[1])
            print(
                f"    {ticker:<34} {len(unexplained):>4} gap(s), "
                f"worst {human(worst[1])} at {worst[0]:%m-%d %H:%M}"
            )
            shown += 1
            if shown >= 15:
                break
        if not shown:
            print("    none. Every per-market gap is explained by a collector outage.")

    print(f"\n{'=' * 68}")
    verdict = (
        "CLEAN"
        if not outages and not dropped and not brief
        else "GAPS PRESENT - see above"
    )
    print(f"VERDICT: {verdict}")
    print(
        "Whatever this says goes in the methodology section verbatim. Reporting\n"
        "coverage honestly costs nothing; being caught assuming it costs the\n"
        "credibility of every result built on the series."
    )
    print(f"{'=' * 68}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--data", type=Path, default=Path("./data"), help="data directory")
    p.add_argument(
        "--gap-threshold",
        type=float,
        default=300.0,
        help="seconds of silence that counts as a gap (default 300, i.e. 5 missed cycles at 60s)",
    )
    p.add_argument(
        "--per-market",
        action="store_true",
        help="also list per-market gaps that no collector outage explains",
    )
    args = p.parse_args(argv)
    return audit(args.data, args.gap_threshold, args.per_market)


if __name__ == "__main__":
    raise SystemExit(main())
