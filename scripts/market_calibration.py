#!/usr/bin/env python3
"""Is the Kalshi market itself calibrated?

Before building a fair-value model to beat a benchmark, measure the benchmark.
A model that scores a Brier of 0.18 means nothing until you know what the market
prices scored on the same events — and if the market is already well calibrated,
the honest conclusion is that the edge is not in probability estimation.

This is also the only calibration study this project can run at scale. The
pinned panel is deliberately long-dated and yields almost no settled outcomes;
the exchange's own settled markets yield thousands.

Method
------
1. Page `/markets` for **settled** markets and keep those with an unambiguous
   `result` and real volume.
2. For each, pull its trade tape once and read off the last trade price at each
   of several horizons before close.
3. Score price-as-probability against the settled outcome with
   `quant.common.statistics.calibration`.

Reading the price at a *horizon* is the whole point. The final trade price of a
settled market is nearly 0 or 100 by construction, so scoring it would measure
nothing but the clock. Calibration at one hour, one day and one week before
close are three different questions, and the way the numbers move between them
is the result.

Three things that would make this lie, and what is done about them
------------------------------------------------------------------
1.  **Survivorship across horizons.** A market that first traded two hours
    before close has no one-week price, so the one-week sample is a different
    and more liquid set of markets. Counts are reported per horizon and the
    intersection sample is scored separately, because a calibration curve that
    improves with horizon may only be reporting which markets survived.
2.  **Trade prices are not mid prices.** They carry the spread and the taker's
    direction. On a market with a 5c spread the traded price is systematically
    off the fair value by up to half of it. Reported, not corrected.
3.  **The base rate is not 0.5.** Most binary event contracts resolve NO. A
    Brier score has no scale on its own, so everything is quoted against
    climatology — always predicting the sample base rate.

Usage
-----
    python scripts/market_calibration.py --max-markets 400
    python scripts/market_calibration.py --analyse-only        # reuse the cache
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv                                      # noqa: E402
from quant.common.api.kalshi import (                               # noqa: E402
    KalshiAPIError, KalshiClient, parse_trade,
)
from quant.ingest.discovery import discover                          # noqa: E402
from quant.common.statistics.calibration import (                   # noqa: E402
    brier_decomposition,
    brier_score,
    log_loss,
    reliability_curve,
    skill_score,
    spiegelhalter_z,
)

log = logging.getLogger("market_calibration")

HORIZONS_HOURS = [1, 6, 24, 72, 168]
BASE_SEED = 20260825


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def probe(client: KalshiClient, status: str) -> int:
    """One request. Print what the endpoint actually returns and stop.

    `scripts/inspect_payload.py` exists because documented Kalshi fields have
    already failed to appear in a live response once. Guessing the shape and
    then paging for an hour before finding out is the expensive way to learn it.
    """
    payload = client.get("/markets", {"limit": 5, "status": status}).payload or {}
    print(f"top-level keys: {sorted(payload.keys())}")
    markets = payload.get("markets") or []
    print(f"markets returned for status={status!r}: {len(markets)}")
    if not markets:
        print("\n  Nothing came back. Try --status finalized, or drop --status")
        print("  entirely and read the `status` values off whatever is returned.")
        return 1
    m = markets[0]
    print(f"\nmarket keys ({len(m)}):")
    for k in sorted(m):
        print(f"  {k:<28} {str(m[k])[:60]}")
    for field in ("result", "close_time", "volume", "status", "ticker"):
        print(f"\n{field!r} present: {field in m}   value: {str(m.get(field))[:50]}")
    return 0


def survey(client: KalshiClient, status: str, pages: int,
           max_close_ts: int | None) -> int:
    """Page a little and report WHAT is in the settled feed, without fetching trades.

    The first real run found 8,000 markets across 40 pages and every one was an
    auto-generated MVE shard. That is not a bug to work around blindly - it is a
    fact about the feed, and the right response is to measure the composition
    before deciding how to sample it. A few requests, no trade calls.
    """
    by_series: dict[str, int] = defaultdict(int)
    shard = real = 0
    volumes: list[float] = []
    oldest = newest = None
    for m in iter_settled_markets(client, status, pages, max_close_ts):
        series = str(m.get("ticker") or "").split("-")[0]
        by_series[series] += 1
        if is_auto_generated(m):
            shard += 1
        else:
            real += 1
            volumes.append(volume_of(m))
        close = parse_time(m.get("close_time"))
        if close:
            oldest = close if oldest is None else min(oldest, close)
            newest = close if newest is None else max(newest, close)

    total = shard + real
    if not total:
        print(f"nothing came back for status={status!r}")
        return 1
    print(f"\n{total:,} markets over {pages} pages")
    print(f"  auto-generated MVE shards : {shard:,} ({shard / total:.1%})")
    print(f"  real markets              : {real:,} ({real / total:.1%})")
    if oldest and newest:
        print(f"  close times span          : {oldest:%Y-%m-%d %H:%M} to {newest:%Y-%m-%d %H:%M}")
    if volumes:
        ordered = sorted(volumes)
        med = ordered[len(ordered) // 2]
        print(f"  real-market volume        : median {med:,.0f}, "
              f"max {ordered[-1]:,.0f}, {sum(v >= 100 for v in volumes):,} at 100+")

    print("\ntop series by count")
    for series, n in sorted(by_series.items(), key=lambda kv: -kv[1])[:15]:
        tag = "  <-- shard factory" if series.startswith("KXMVE") else ""
        print(f"  {series:<34} {n:>7,}{tag}")

    print("\nWHAT THIS MEANS")
    if real == 0:
        print("  The feed is entirely shards at this point in the cursor. Either page")
        print("  much deeper (--max-pages), or move the window with")
        print("  --close-before-days to sample settlements from before the shard")
        print("  product existed.")
    elif real / total < 0.05:
        print(f"  Real markets are {real / total:.1%} of the feed, so reaching 1,600")
        print(f"  candidates needs roughly {int(1600 / (real / total) / 200):,} pages.")
        print("  Use --close-before-days to find a window with a better ratio.")
    else:
        print(f"  Real markets are {real / total:.1%} of the feed. Sampling straight")
        print("  through is viable; set --max-pages accordingly.")
    return 0


def iter_settled_markets(client: KalshiClient, status: str, max_pages: int,
                         max_close_ts: int | None = None) -> Iterable[dict]:
    """Page `/markets`, following the cursor, with a hard page cap.

    The cap matters. Kalshi's settled history is very large, and the first
    version of this paged all of it before filtering - 75 minutes of HTTP 429
    to discover the field names were wrong. The caller stops as soon as it has
    enough candidates rather than collecting everything first.
    """
    cursor: str | None = None
    seen: set[str] = set()
    for page in range(max_pages):
        params: dict[str, Any] = {"limit": 200, "status": status}
        if max_close_ts is not None:
            params["max_close_ts"] = int(max_close_ts)
        if cursor:
            params["cursor"] = cursor
        payload = client.get("/markets", params).payload or {}
        markets = payload.get("markets") or []
        if not markets:
            log.info("page %d came back empty; stopping", page + 1)
            return
        yield from markets
        cursor = payload.get("cursor") or None
        if not cursor or cursor in seen:
            return
        seen.add(cursor)
    log.warning("hit the %d-page cap; raise --max-pages for a larger sample", max_pages)


def volume_of(market: dict) -> float:
    """Traded contracts. The field is `volume_fp`, not `volume`.

    The first version read `volume` and got None for every market, so the
    minimum-volume filter rejected all of them and the run reported "0 settled
    markets" after paging for an hour. `_first_present` in the API client exists
    for exactly this: Kalshi's live payloads carry unit-suffixed names that some
    documentation omits.
    """
    for name in ("volume_fp", "volume", "volume_24h_fp"):
        value = market.get(name)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


def is_auto_generated(market: dict) -> bool:
    """Multi-variate-event shards: auto-created parlays, not tradeable markets.

    The probe's first sample was `KXMVECROSSCATEGORY-SHARD1-...`, titled
    "yes Rinky Hijikata, yes Mariano Navone, yes Tampa Bay, ...", created at
    22:20:03 and settled at 22:25:25 with zero volume. These combination markets
    are generated in bulk and dominate the settled feed. Scoring them would
    measure a market that never traded.
    """
    return bool(market.get("mve_collection_ticker")) or bool(market.get("is_provisional"))


def real_series(client: KalshiClient, exclude_prefix: str = "KXMVE") -> list[str]:
    """Series tickers of genuinely traded families, from the open universe.

    Paging the settled feed does not work: it is 98.9-100% auto-generated MVE
    shards at every window tried, and ten pages span eight minutes of close
    times. Reaching a useful sample that way would need ~700 pages covering
    half a day, which is a sample of one afternoon rather than of the exchange.

    Going the other way round is targeted. `discover()` already enumerates the
    live families - 5,518 of them, verified in the field-size scan - and their
    series tickers name the real products. Settled markets are then queried per
    series, which the shard factories are not part of.
    """
    families, _ = discover(client, max_legs=None, statuses=None)
    series = sorted({f.series_ticker for f in families
                     if f.series_ticker and not f.series_ticker.startswith(exclude_prefix)})
    # Shuffle before the caller truncates. Returning them sorted and taking the
    # first N is an ALPHABETICAL slice, not a sample: the first run of this took
    # 120 series alphabetically, ~100 of which had no settled markets, and ended
    # up with 210 markets from exactly two series - KXAFLGAME (79%) and
    # KXACTBLUETOP (21%). Australian rules football is not "the Kalshi market".
    random.Random(BASE_SEED).shuffle(series)
    log.info("%d live families -> %d distinct real series (shuffled, seed %d)",
             len(families), len(series), BASE_SEED)
    return series


def settled_in_series(client: KalshiClient, series: str, status: str,
                      pages: int = 2) -> list[dict]:
    """Settled markets for one series. Verifies the filter was actually applied.

    A server that ignores an unknown query parameter returns the unfiltered
    firehose, and the caller cannot tell the difference from a series that
    happens to be busy. Checking that the tickers match is cheap and turns a
    silent wrong answer into a loud one.
    """
    out: list[dict] = []
    cursor: str | None = None
    for page in range(pages):
        params: dict[str, Any] = {"limit": 200, "status": status, "series_ticker": series}
        if cursor:
            params["cursor"] = cursor
        payload = client.get("/markets", params).payload or {}
        markets = payload.get("markets") or []
        if not markets:
            break
        if page == 0:
            matching = sum(1 for m in markets
                           if str(m.get("ticker") or "").startswith(series))
            if matching < len(markets) * 0.5:
                raise SystemExit(
                    f"`series_ticker` appears to be ignored: asked for {series!r} and "
                    f"only {matching}/{len(markets)} returned tickers match. Stopping "
                    "rather than scoring an unfiltered sample."
                )
        out.extend(markets)
        cursor = payload.get("cursor") or None
        if not cursor:
            break
    return out


def outcome_of(market: dict) -> int | None:
    """1 for YES, 0 for NO, None for anything else.

    Voided and blank results are dropped rather than guessed. A voided market
    scored as NO would quietly reward every model that was bearish on it.
    """
    result = str(market.get("result") or "").strip().lower()
    return {"yes": 1, "no": 0}.get(result)


def price_at_horizons(trades: list[tuple[float, float]], close_ts: float,
                      horizons_hours: Iterable[int]) -> dict[str, float]:
    """Last trade price at or before each horizon, as a probability.

    `trades` is (unix_seconds, price_cents), any order. Returns only horizons
    that actually have a trade before them - a missing key means the market had
    not traded that far out, which is information rather than a zero.
    """
    if not trades:
        return {}
    ordered = sorted(trades)
    out: dict[str, float] = {}
    for hours in horizons_hours:
        cutoff = close_ts - hours * 3600.0
        prior = [p for t, p in ordered if t <= cutoff]
        if prior:
            out[str(hours)] = prior[-1] / 100.0
    return out


class CorruptCache(RuntimeError):
    """The cache exists and cannot be read.

    Deliberately fatal. The alternative — logging a warning and starting fresh
    — silently discards however many hours of collection are in that file and
    then overwrites it, which turns one interrupted write into total data loss.
    Deleting the file is a decision for whoever is watching, not for the script.
    """


def load_cache(cache: Path) -> tuple[list[dict], set[str]]:
    """Existing records and every ticker already attempted.

    The two are not the same set, and that difference is the whole value of the
    resume. A market whose trade tape yields no price at any horizon produces
    no record, but it still cost the API calls to find that out. Reconstructing
    "seen" from the records alone would re-fetch every one of those on every
    restart — and in the 398-market run they outnumbered the usable markets.
    """
    if not cache.exists():
        return [], set()
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CorruptCache(
            f"{cache} exists but is not readable JSON ({exc}). It holds however "
            "much collection has already happened. Inspect it, and delete or move "
            "it deliberately if it is genuinely lost — this script will not "
            "overwrite it for you."
        ) from exc

    records = data.get("records") or []
    attempted = set(data.get("attempted_tickers") or [])
    attempted |= {r.get("ticker") for r in records if r.get("ticker")}
    attempted.discard(None)
    log.info("resuming: %d records already collected, %d tickers already attempted",
             len(records), len(attempted))
    return records, attempted


def save_cache(cache: Path, status: str, min_volume: float,
               records: list[dict], attempted: set[str]) -> None:
    """Write the cache atomically.

    A plain `write_text` on a multi-megabyte file is not atomic: interrupt it
    and what is left on disk is half a JSON document. Since the point of
    flushing every N markets is to survive an interruption, doing it
    non-atomically would make the crash window *more* dangerous the more often
    it was flushed. Written to a sibling temp file and renamed, which is atomic
    on both POSIX and Windows via os.replace.
    """
    cache.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "min_volume": min_volume,
        "horizons_hours": HORIZONS_HOURS,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
        "attempted_tickers": sorted(attempted),
    }
    tmp = cache.with_suffix(cache.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=float), encoding="utf-8")
    os.replace(tmp, cache)


def collect(client: KalshiClient, status: str, max_markets: int,
            min_volume: float, cache: Path, max_pages: int,
            max_close_ts: int | None, by_series: bool = True,
            max_series: int = 120) -> list[dict]:
    log.info("paging settled markets (status=%r, max %d pages)", status, max_pages)

    records, attempted = load_cache(cache)
    already = len(records)
    remaining = max_markets - already
    if remaining <= 0:
        log.info("cache already holds %d records, at or above the target of %d; "
                 "nothing to collect", already, max_markets)
        return records
    if already:
        log.info("%d still to collect to reach %d", remaining, max_markets)

    # `max_markets` is the target for the WHOLE collection, not for this run.
    # Treating it as a per-run figure means every resume adds another full
    # target and the sample size becomes a function of how often the run
    # happened to crash.
    target = remaining * 4
    candidates: list[dict] = []
    seen_status: dict[str, int] = defaultdict(int)
    skipped: dict[str, int] = defaultdict(int)
    if by_series:
        source: Iterable[dict] = []
        series_list = real_series(client)[:max_series]
        collected: list[dict] = []
        for i, series in enumerate(series_list, 1):
            try:
                collected.extend(settled_in_series(client, series, status))
            except KalshiAPIError as exc:
                log.warning("%s: %s", series, exc)
            if i % 20 == 0:
                log.info("  %d/%d series, %d settled markets so far",
                         i, len(series_list), len(collected))
            if len(collected) >= target * 3:
                break
        source = collected
        log.info("%d settled markets across %d series", len(collected), len(series_list))
    else:
        source = iter_settled_markets(client, status, max_pages, max_close_ts)

    for m in source:
        seen_status[str(m.get("status"))] += 1
        y = outcome_of(m)
        close = parse_time(m.get("close_time"))
        opened = parse_time(m.get("open_time"))
        if y is None or close is None:
            skipped["no result or close time"] += 1
            continue
        if is_auto_generated(m):
            skipped["auto-generated MVE shard"] += 1
            continue
        if volume_of(m) < min_volume:
            skipped[f"volume below {min_volume:g}"] += 1
            continue
        # A market that existed for ten minutes cannot have a one-week price,
        # and its presence would make the short-horizon sample look artificially
        # large relative to the long-horizon one.
        if opened is not None and (close - opened).total_seconds() < 3600:
            skipped["open for under an hour"] += 1
            continue
        if m.get("ticker") in attempted:
            skipped["already attempted in an earlier run"] += 1
            continue
        candidates.append({
            "ticker": m.get("ticker"),
            "series": (m.get("ticker") or "").split("-")[0],
            "outcome": y,
            "close_ts": close.timestamp(),
            "volume": volume_of(m),
        })
        if len(candidates) >= target:
            log.info("reached %d candidates; stopping the scan early", target)
            break
    log.info("%d usable markets (statuses seen: %s)", len(candidates), dict(seen_status))
    for reason, count in sorted(skipped.items(), key=lambda kv: -kv[1]):
        log.info("  skipped %6d: %s", count, reason)
    if not candidates and not records:
        raise SystemExit(
            f"no usable markets for status={status!r}. Statuses actually seen: "
            f"{dict(seen_status) or 'none - the endpoint returned nothing'}.\n"
            "Run with --probe first: it makes ONE request and prints the real "
            "field names, which is how to tell a wrong status filter from a "
            "wrong field name."
        )
    if not candidates:
        log.info("nothing new to collect; returning the %d cached records", len(records))
        return records

    # Sample at random, not by volume: ranking on volume would select the most
    # liquid markets, which are exactly the ones most likely to be calibrated,
    # and the result would be an artifact of the selection.
    # The seed is fixed, but a resumed run shuffles a different candidate list
    # (the attempted ones are gone), so the sample is not reproducible from the
    # seed alone across restarts. It is still a random sample of what remains,
    # which is what matters for the estimate; it is not a replayable one.
    rng = random.Random(BASE_SEED)
    rng.shuffle(candidates)
    chosen = candidates[:remaining]
    log.info("sampling %d of them (seed %d)", len(chosen), BASE_SEED)

    longest = max(HORIZONS_HOURS) * 3600.0
    flush_every = 50
    for i, c in enumerate(chosen, 1):
        try:
            trades: dict[str, tuple[float, float]] = {}
            for page in client.iter_all_trades(
                ticker=c["ticker"],
                min_ts=int(c["close_ts"] - longest - 86400),
                max_ts=int(c["close_ts"]),
            ):
                for t in (page.payload or {}).get("trades") or []:
                    # parse_trade handles yes_price_dollars vs yes_price and
                    # created_time vs ts. The hand-rolled version read
                    # `yes_price` only, which is absent from the dollars-shaped
                    # payload the probe showed - it would have returned nothing
                    # for every market, silently.
                    try:
                        parsed = parse_trade(t)
                    except (ValueError, KeyError):
                        continue
                    tid = t.get("trade_id") or f"{parsed.timestamp}-{parsed.price}"
                    trades[tid] = (parsed.timestamp.timestamp(), parsed.price)
        except KalshiAPIError as exc:
            log.warning("%s: trades unavailable (%s)", c["ticker"], exc)
            continue

        # Marked attempted whether or not it yielded a price. A market with no
        # trade in any horizon window is a permanent answer, not a transient
        # failure, and re-asking it on every restart is exactly the waste the
        # resume exists to remove. The API error above is NOT marked, because
        # that one may well succeed next time.
        attempted.add(c["ticker"])
        prices = price_at_horizons(list(trades.values()), c["close_ts"], HORIZONS_HOURS)
        if prices:
            records.append({**c, "n_trades": len(trades), "prices": prices})
        if i % flush_every == 0:
            save_cache(cache, status, min_volume, records, attempted)
            log.info("  flushed at %d/%d: %d records (%d this run)",
                     i, len(chosen), len(records), len(records) - already)
        elif i % 25 == 0:
            log.info("  %d/%d markets, %d records (%d this run)",
                     i, len(chosen), len(records), len(records) - already)

    save_cache(cache, status, min_volume, records, attempted)
    log.info("wrote %s (%d markets, %d added this run)",
             cache, len(records), len(records) - already)
    return records


def score(pairs: list[tuple[float, int]], label: str) -> dict[str, Any]:
    probs = [p for p, _ in pairs]
    outs = [y for _, y in pairs]
    bs = brier_score(probs, outs)
    base = sum(outs) / len(outs)
    climatology = base * (1.0 - base)
    d = brier_decomposition(probs, outs, bins=10)
    z, pval = spiegelhalter_z(probs, outs)
    return {
        "label": label, "n": len(pairs), "base_rate": base,
        "brier": bs, "log_loss": log_loss(probs, outs),
        "skill_vs_climatology": skill_score(bs, climatology) if climatology else float("nan"),
        "reliability": d.reliability, "resolution": d.resolution,
        "uncertainty": d.uncertainty, "residual": d.residual,
        "spiegelhalter_z": z, "spiegelhalter_p": pval,
        "curve": [vars(b) for b in reliability_curve(probs, outs, bins=10, min_count=20)],
    }


def composition(records: list[dict]) -> dict[str, Any]:
    """What is actually in the sample. Printed before any score.

    A calibration number computed on two series is a fact about those two
    series. The first run of this scored 210 markets and reported the market
    "systematically over-confident" at p = 0.037; the sample was 79% Australian
    rules football and the longshot bin contained a single event. Concentration
    has to be in front of the reader, not one level down in a JSON file.
    """
    by_series: dict[str, int] = defaultdict(int)
    for r in records:
        by_series[r["series"]] += 1
    ranked = sorted(by_series.items(), key=lambda kv: -kv[1])
    total = len(records)
    top = ranked[0][1] / total if ranked else 0.0
    extreme = [r["prices"]["1"] for r in records if "1" in r["prices"]]
    return {
        "n": total,
        "distinct_series": len(by_series),
        "top_series_share": top,
        "top": ranked[:8],
        "fraction_outside_10_90": (sum(p < 0.1 or p > 0.9 for p in extreme) / len(extreme)
                                   if extreme else float("nan")),
    }


def analyse(records: list[dict]) -> dict[str, Any]:
    by_horizon: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for r in records:
        for h, p in r["prices"].items():
            by_horizon[h].append((p, r["outcome"]))

    results = {h: score(by_horizon[h], f"{h}h before close")
               for h in sorted(by_horizon, key=int) if len(by_horizon[h]) >= 50}

    # Same markets at every horizon, so a change across horizons cannot be the
    # sample changing underneath it.
    complete = [r for r in records if len(r["prices"]) == len(HORIZONS_HOURS)]
    common = {h: score([(r["prices"][h], r["outcome"]) for r in complete],
                       f"{h}h (common sample)")
              for h in sorted(by_horizon, key=int)} if len(complete) >= 50 else {}

    return {"by_horizon": results, "common_sample": common,
            "composition": composition(records),
            "n_markets": len(records), "n_complete": len(complete)}


def report(out: dict[str, Any]) -> None:
    c = out["composition"]
    print(f"\n{out['n_markets']} settled markets, {out['n_complete']} with a price "
          f"at every horizon")
    print(f"\nSAMPLE COMPOSITION — read this before the scores")
    print(f"  distinct series      {c['distinct_series']}")
    print(f"  largest series share {c['top_series_share']:.0%}")
    print(f"  priced outside 10-90c at 1h  {c['fraction_outside_10_90']:.0%}")
    for series, n in c["top"]:
        print(f"    {series:<30} {n:>5}  ({n / c['n']:.0%})")
    warnings = []
    if c["distinct_series"] < 10:
        warnings.append(f"only {c['distinct_series']} series - this measures those "
                        "series, not the exchange")
    if c["top_series_share"] > 0.4:
        warnings.append(f"one series is {c['top_series_share']:.0%} of the sample")
    if c["fraction_outside_10_90"] > 0.5:
        warnings.append(f"{c['fraction_outside_10_90']:.0%} of prices are outside "
                        "10-90c, so most markets were near-decided and the skill "
                        "figure is mostly the clock")
    if out["n_complete"] < 50:
        warnings.append(f"only {out['n_complete']} markets have a price at every "
                        "horizon, so the horizon comparison has NO survivorship control")
    out["warnings"] = warnings
    if warnings:
        print("\n  ** DO NOT REPORT THESE NUMBERS AS A CALIBRATION FINDING **")
        for w in warnings:
            print(f"     - {w}")
    print()
    print(f"{'horizon':>18} {'n':>6} {'base':>6} {'Brier':>8} {'logloss':>8} "
          f"{'skill':>7} {'REL':>7} {'RES':>7} {'z':>7} {'p':>8}")
    for block in (out["by_horizon"], out["common_sample"]):
        if not block:
            continue
        for h in sorted(block, key=int):
            r = block[h]
            print(f"{r['label']:>18} {r['n']:>6,} {r['base_rate']:>6.3f} "
                  f"{r['brier']:>8.4f} {r['log_loss']:>8.4f} "
                  f"{r['skill_vs_climatology']:>+7.3f} {r['reliability']:>7.4f} "
                  f"{r['resolution']:>7.4f} {r['spiegelhalter_z']:>+7.2f} "
                  f"{r['spiegelhalter_p']:>8.4f}")
        print()

    horizons = out["by_horizon"]
    if not horizons:
        print("  too few markets at any horizon to score")
        return
    nearest = horizons[min(horizons, key=int)]
    print(f"Reliability at {nearest['label']} (bins with 20+ outcomes)")
    print(f"  {'range':>12} {'n':>6} {'forecast':>9} {'observed':>9} {'gap':>8}  95% CI")
    for b in nearest["curve"]:
        flag = "  <-- outside CI" if not (b["ci_low"] <= b["mean_forecast"] <= b["ci_high"]) else ""
        print(f"  {b['lower']:.2f}-{b['upper']:.2f} {b['n']:>6,} "
              f"{b['mean_forecast']:>9.3f} {b['observed_frequency']:>9.3f} "
              f"{b['observed_frequency'] - b['mean_forecast']:>+8.3f}  "
              f"[{b['ci_low']:.3f}, {b['ci_high']:.3f}]{flag}")

    print("\nVERDICT")
    c = out["composition"]
    # Any warning printed above withholds the verdict. The previous version
    # gated only on series concentration, so a run could print
    # "DO NOT REPORT THESE NUMBERS" and then state a conclusion four lines
    # later. A guard that the conclusion ignores is decoration.
    if out.get("warnings"):
        print("  Withheld - the composition block above lists unmet conditions:")
        for w in out["warnings"]:
            print(f"     - {w}")
        print("\n  The scores are printed for diagnosis, not for quoting.")
        return
    if c["distinct_series"] < 10 or c["top_series_share"] > 0.4:
        print("  Withheld. The sample is too concentrated for a statement about the")
        print("  exchange to mean anything - see the composition block above. Raise")
        print("  --max-series and rerun before reading anything into the scores.")
        return
    z, p = nearest["spiegelhalter_z"], nearest["spiegelhalter_p"]
    if p < 0.05:
        direction = "UNDER-confident (outcomes beat prices)" if z > 0 else \
                    "OVER-confident (prices beat outcomes)"
        print(f"  Calibration is rejected at {nearest['label']}: z = {z:+.2f}, p = {p:.4f}.")
        print(f"  The market is systematically {direction}.")
        print("  A fair-value model has something to correct. Check the reliability")
        print("  table above for WHERE - a uniform bias is a different opportunity")
        print("  from a favourite-longshot skew, and the fee ceiling may eat either.")
    else:
        print(f"  Calibration is not rejected at {nearest['label']}: z = {z:+.2f}, "
              f"p = {p:.4f}.")
        print("  The market prices are consistent with the outcomes. A fair-value")
        print("  model would have to beat a calibrated benchmark, which means the")
        print("  edge is not in probability estimation - it is in execution, or")
        print("  it is not there. That is a finding, and it belongs in the write-up.")
    print(f"\n  Skill against climatology: {nearest['skill_vs_climatology']:+.3f} "
          f"(0 = no better than always predicting the base rate of "
          f"{nearest['base_rate']:.3f})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--status", default="settled", help="'settled' or 'finalized'")
    ap.add_argument("--max-markets", type=int, default=400)
    ap.add_argument("--min-volume", type=float, default=100.0,
                    help="traded contracts; reads volume_fp, not volume")
    ap.add_argument("--cache", type=Path,
                    default=Path("results/kalshi/settled_prices.json"))
    ap.add_argument("--out", type=Path,
                    default=Path("results/kalshi/market_calibration.json"))
    ap.add_argument("--analyse-only", action="store_true", help="reuse the cache")
    ap.add_argument("--probe", action="store_true",
                    help="one request; print the real payload shape and exit")
    ap.add_argument("--survey", type=int, metavar="PAGES", default=0,
                    help="page this many and report the feed's composition, then exit")
    ap.add_argument("--close-before-days", type=float, default=None,
                    help="only markets that closed at least this many days ago")
    ap.add_argument("--page-feed", action="store_true",
                    help="page the settled feed instead of querying by series "
                         "(the feed is 99%% auto-generated shards; not recommended)")
    ap.add_argument("--max-series", type=int, default=400,
                    help="series to query; most have no settled markets, so this "
                         "needs to be large for a diverse sample")
    ap.add_argument("--max-pages", type=int, default=40,
                    help="hard cap on pages of /markets (200 per page)")
    ap.add_argument("--rate", type=float, default=2.0,
                    help="requests per second; the client default of 8 draws HTTP 429")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--env-file", type=Path, default=Path(".env"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    if args.analyse_only:
        if not args.cache.exists():
            raise SystemExit(f"no cache at {args.cache}; run without --analyse-only first")
        records = json.loads(args.cache.read_text(encoding="utf-8"))["records"]
        log.info("loaded %d markets from cache", len(records))
    else:
        load_dotenv(args.env_file)
        client = KalshiClient.from_env(demo=args.demo, rate_per_sec=args.rate)
        if args.probe:
            return probe(client, args.status)
        max_close_ts = None
        if args.close_before_days is not None:
            max_close_ts = int(datetime.now(timezone.utc).timestamp()
                               - args.close_before_days * 86400)
            log.info("restricting to markets closed before %s",
                     datetime.fromtimestamp(max_close_ts, timezone.utc)
                     .strftime("%Y-%m-%d"))
        if args.survey:
            return survey(client, args.status, args.survey, max_close_ts)
        records = collect(client, args.status, args.max_markets, args.min_volume,
                          args.cache, args.max_pages, max_close_ts,
                          by_series=not args.page_feed, max_series=args.max_series)

    if len(records) < 50:
        raise SystemExit(f"only {len(records)} usable markets; too few to calibrate")

    out = analyse(records)
    report(out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
