#!/usr/bin/env python3
"""Measure the two interfaces the temperature model needs, before building on them.

This script asks questions. It does not fit anything, trade anything, or write
a result. It exists because four separate failures in this project came from
building on an assumed payload shape: guessed `volume` where the API returns
`volume_fp`, guessed `yes_price` where it returns `yes_price_dollars`, an
alphabetical slice mistaken for a sample. The model in
`src/quant/kalshi/models/temperature.py` is written against *no* assumed field
names, and this is what supplies the real ones.

Findings from the first run (2026-08-25) are folded in below; the questions
that produced them are kept because they have to be re-asked whenever the
exchange changes anything.

Six questions, in the order the model needs them answered:

1. Which temperature series does the exchange actually list, and what are their
   tickers? Not guessed from `KXHIGH*`; enumerated.
2. What does one full market payload contain? Every field name, verbatim.
3. How is a bucket expressed? `floor_strike`/`cap_strike`, a subtitle to parse,
   or something else — and are the open end buckets marked?
4. What are the settlement rules? The model's entire rounding argument rests on
   the settled value being a whole degree from a named station. If the rules
   say otherwise, the half-degree boundaries in `Bucket.continuous_bounds` are
   wrong and the model is biased on every contract.
5. What does the NWS forecast payload look like for that station, and what lead
   times does it cover?
6. Is there a retrievable observation history? That is the "observed" half of
   the (forecast, observed) pairs, and without it there is no error model and
   so no fair value.

Output goes to `results/kalshi/weather_probe.json` plus a readable summary on
stdout. All of it is public market and weather data.

What the first run changed
--------------------------
- Paging `/events` found nothing and said so quietly: the scan stopped at its
  own page cap, 2,400 events in, without reporting that the cursor was still
  live. Series are now enumerated through `GET /series`, which is one request,
  and every paged loop reports whether it finished or hit its cap.
- Matching series names on substrings put `KXKELOWNAMAYOR`, `KXLOWA`,
  `KXNFLOWNERSTAKE` and `KXWYUNEMPLOW` in a temperature report, all on the
  "LOW" inside another word. Selection is now by the series `category` field
  the exchange itself sets, with the name hints kept only as a fallback.
- Settlement is the **NWS Daily Climate Report**, issued the following
  morning — not the station observation feed. That feed returns Celsius, and a
  maximum taken over hourly observations is not the number that settles the
  contract. The observed half of every (forecast, observed) pair has to come
  from the climate report.
- The daily maximum runs midnight to midnight local, **except under Daylight
  Saving Time, when it runs 01:00 to 00:59 the following day**. August is DST.
  A pair collector that assumes calendar days is wrong for eight months a year.

Usage
-----
    python scripts/probe_weather.py                  # everything
    python scripts/probe_weather.py --kalshi-only
    python scripts/probe_weather.py --nws-only
    python scripts/probe_weather.py --city chicago
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

OUT_PATH = Path("results/kalshi/weather_probe.json")

#: Substrings that suggest a series is about temperature. Deliberately broad —
#: the point of the probe is to find out what the naming actually is, so a
#: narrow filter here would defeat it. Anything matched is reported, not used.
TEMPERATURE_HINTS = ("HIGH", "TEMP", "LOW", "HEAT", "COLD", "DEGREE")

#: NWS asks for a contact address in the User-Agent and rate-limits anonymous
#: clients that omit it. This is a courtesy header, not a credential.
NWS_AGENT = "quant-research temperature study (contact via github.com/Dodaflop1)"

#: Cities Kalshi has historically listed daily-high markets for. Used only to
#: pick a coordinate to probe the NWS side with; the Kalshi side enumerates.
PROBE_CITIES = {
    "nyc": (40.7790, -73.9692),
    "chicago": (41.7860, -87.7524),
    "miami": (25.7906, -80.3164),
    "austin": (30.1830, -97.6800),
    "denver": (39.8466, -104.6562),
    "philadelphia": (39.8683, -75.2311),
    "los_angeles": (33.9382, -118.3866),
}


# =============================================================================
# Kalshi
# =============================================================================


def _paged(client, path: str, params: dict, key: str, max_pages: int) -> tuple[list, bool]:
    """Follow a cursor to exhaustion, and say whether it got there.

    The first version of this probe stopped at its page cap and printed only
    how many records it had seen, so "no temperature series exist" and "the
    scan ran out of pages" looked identical. They are not the same fact and
    the caller is told which one it got.
    """
    rows: list = []
    cursor: Optional[str] = None
    for _ in range(max_pages):
        query = dict(params)
        if cursor:
            query["cursor"] = cursor
        body = client.get(path, query).payload
        rows.extend(body.get(key) or [])
        cursor = body.get("cursor") or None
        if not cursor:
            return rows, False
    return rows, True


def settlement_of(row: dict) -> dict[str, Any]:
    """Who settles this series, and — if the NWS — which climate-report location.

    The settlement URLs carry the answer literally:
    `product.php?site=OKX&product=CLI&issuedby=NYC`. `site` is the issuing
    office and `issuedby` is the CLI location id, which is what
    `/products/types/CLI/locations/{id}` wants. The first version of this probe
    passed the station id and then the office and got HTTP 400 twice, because
    it is neither.
    """
    sources = row.get("settlement_sources") or []
    names = " ".join(str(s.get("name") or "") for s in sources).lower()
    urls = " ".join(str(s.get("url") or "") for s in sources)

    if "weather company" in names or "weather channel" in names or "weather.com" in urls:
        who = "The Weather Company (proprietary)"
    elif "weather service" in names or "weather.gov" in urls:
        who = "National Weather Service"
    elif not sources:
        who = "none listed"
    else:
        who = sources[0].get("name") or "other"

    return {
        "settles_on": who,
        "cli_locations": sorted(set(re.findall(r"issuedby=([A-Z]{3,4})", urls))),
        "cli_offices": sorted(set(re.findall(r"site=([A-Z]{3,4})", urls))),
        "urls": [str(s.get("url") or "") for s in sources][:4],
    }


def probe_kalshi(demo: bool = False, max_pages: int = 40) -> dict[str, Any]:
    """Enumerate series and report the ones the exchange itself calls weather.

    `GET /series` returns every series with its own `category`, `tags` and
    `settlement_sources` in one request, which is both cheaper and more honest
    than inferring a category from a ticker. The name hints stay as a fallback
    for the case where the category field is empty, and anything they match
    that the category did not is reported separately rather than merged in —
    substring matching on "LOW" once put a mayoral election in a temperature
    report.
    """
    from dotenv import load_dotenv
    from quant.common.api.kalshi import KalshiClient

    if Path(".env").exists():
        load_dotenv(".env")
    # 2.0 rather than the client default of 8. The default earned 90 minutes of
    # HTTP 429 on this API once already.
    client = KalshiClient.from_env(demo=demo, rate_per_sec=2.0)

    series, truncated = _paged(client, "/series", {"limit": 200}, "series", max_pages)
    out: dict[str, Any] = {
        "n_series": len(series),
        "series_listing_truncated": truncated,
        "categories": {},
        "by_category": [],
        "by_name_hint_only": [],
        "sample_event": None,
        "sample_market": None,
        "bucket_source_series": None,
    }

    for row in series:
        category = str(row.get("category") or "")
        out["categories"][category] = out["categories"].get(category, 0) + 1

    def summarise(row: dict) -> dict[str, Any]:
        return {
            "ticker": row.get("ticker"),
            "title": row.get("title"),
            "category": row.get("category"),
            "frequency": row.get("frequency"),
            "tags": row.get("tags"),
            "settlement_sources": row.get("settlement_sources"),
            "contract_terms_url": row.get("contract_terms_url"),
            **settlement_of(row),
        }

    weatherish = ("climate", "weather", "temperature")
    for row in series:
        ticker = str(row.get("ticker") or "").upper()
        haystack = " ".join(str(row.get(f) or "") for f in ("category", "title", "tags"))
        by_category = any(w in haystack.lower() for w in weatherish)
        by_name = any(hint in ticker for hint in TEMPERATURE_HINTS)
        if by_category:
            out["by_category"].append(summarise(row))
        elif by_name:
            out["by_name_hint_only"].append(summarise(row))

    # Which daily temperature series settle on a source with free history, and
    # which on a proprietary one? That distinction decides whether the fair
    # value model is buildable from public data at all, so it is counted rather
    # than eyeballed.
    daily_temp = [r for r in out["by_category"]
                  if r.get("frequency") == "daily"
                  and any(h in str(r["ticker"]).upper() for h in ("HIGH", "LOW", "TEMP"))]
    by_source: dict[str, list[str]] = {}
    for r in daily_temp:
        by_source.setdefault(r["settles_on"], []).append(r["ticker"])
    out["daily_temperature"] = {
        "n": len(daily_temp),
        "by_settlement_source": {k: sorted(v) for k, v in sorted(by_source.items())},
        "nws_settled": sorted(r["ticker"] for r in daily_temp
                              if r["settles_on"] == "National Weather Service"),
        "cli_locations": sorted({loc for r in daily_temp for loc in r["cli_locations"]}),
    }

    # Prefer a series that settles on the NWS AND names a CLI location, since
    # that is the only kind whose observed half is retrievable for free.
    ranked = sorted(
        daily_temp,
        key=lambda r: (r["settles_on"] != "National Weather Service",
                       not r["cli_locations"],
                       "HIGH" not in str(r["ticker"]).upper(),
                       str(r["ticker"])),
    )
    for candidate in ranked[:6]:
        body = client.get("/events", {
            "limit": 20, "series_ticker": candidate["ticker"],
            "status": "open", "with_nested_markets": "true",
        }).payload
        events = [e for e in (body.get("events") or []) if e.get("markets")]
        out.setdefault("bucket_source_tried", []).append(
            {"ticker": candidate["ticker"], "open_events_with_markets": len(events)})
        if events:
            out["bucket_source_series"] = candidate["ticker"]
            out["sample_event"] = events[0]
            out["sample_market"] = events[0]["markets"][0]
            break
    else:
        out["bucket_source_series"] = None

    return out


def describe_buckets(event: dict) -> dict[str, Any]:
    """How is a bucket expressed in the payload?

    Reports every candidate field and whether it is populated, rather than
    picking one. The parser gets written after this has been read, not before.
    """
    markets = event.get("markets") or []
    fields = ("floor_strike", "cap_strike", "strike_type", "subtitle",
              "yes_sub_title", "title", "ticker", "rules_primary")

    rows = []
    for m in markets:
        rows.append({f: m.get(f) for f in fields if f in m})

    present = {f: sum(1 for m in markets if m.get(f) is not None) for f in fields}
    return {
        "n_markets": len(markets),
        "field_population": present,
        "all_market_fields": sorted({k for m in markets for k in m}),
        "rows": rows,
        # An open-ended bucket should have one strike absent. If neither end of
        # the family is open, either the payload marks it differently or the
        # family is not a partition — and `bucket_probabilities` will refuse it.
        "rows_missing_floor": sum(1 for m in markets if m.get("floor_strike") is None),
        "rows_missing_cap": sum(1 for m in markets if m.get("cap_strike") is None),
    }


def describe_settlement(market: dict) -> dict[str, Any]:
    """Pull out the text that says what settles this contract.

    The model rounds to whole degrees because the station reports whole
    degrees. If these rules name a different source, a different rounding, or
    an average rather than a maximum, then `Bucket.continuous_bounds` is wrong
    by half a degree on every bucket — a bias that no amount of good fitting
    would recover, and that would show up as a small, consistent, plausible
    edge in one direction.
    """
    text_fields = {
        k: v for k, v in market.items()
        if isinstance(v, str) and ("rule" in k.lower() or "settle" in k.lower()
                                   or "source" in k.lower())
    }
    blob = " ".join(text_fields.values())
    return {
        "fields": text_fields,
        "mentions_station_id": re.findall(r"\b[KP][A-Z]{3}\b", blob)[:10],
        "mentions_degrees": "degree" in blob.lower() or "°" in blob,
        "mentions_nearest_whole": bool(re.search(r"whole|nearest|round", blob, re.I)),
        "mentions_maximum": bool(re.search(r"\bmax(imum)?\b|\bhigh(est)?\b", blob, re.I)),
    }


# =============================================================================
# National Weather Service
# =============================================================================


def _get_json(url: str, timeout: float = 30.0, retries: int = 3) -> Any:
    request = urllib.request.Request(url, headers={
        "User-Agent": NWS_AGENT,
        "Accept": "application/geo+json",
    })
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                last = exc
                continue
            raise
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"unreachable: {last}")


def probe_climate_report(location: str, station: str = "") -> dict[str, Any]:
    """The Daily Climate Report — the product that actually settles the contract.

    Kalshi settles daily temperature markets on "the final climate report
    issued by the National Weather Service, typically released the following
    morning". That is the CLI product, not the station observation feed. The
    difference is not cosmetic:

    - the observation feed reports Celsius, the report reports Fahrenheit;
    - a maximum taken over hourly observations can miss a spike between them;
    - the report's day is midnight to midnight local **except under Daylight
      Saving Time, when it runs 01:00 to 00:59 the next day**.

    Any of the three produces a value that is close to the settled one, agrees
    with it most days, and disagrees exactly when the contract was interesting.

    This reports the product's raw text and where the maximum appears in it. It
    deliberately does not write a parser: the format is fixed-width plain text
    and the parser gets written against a printed sample, not a guess.
    """
    out: dict[str, Any] = {"location": location, "station": station}
    try:
        listing = _get_json(
            f"https://api.weather.gov/products/types/CLI/locations/{location}?limit=5"
        )
    except Exception as exc:  # noqa: BLE001 - a probe reports its own failure
        out["error"] = repr(exc)
        return out

    graph = listing.get("@graph") or listing.get("features") or []
    out["n_recent_products"] = len(graph)
    out["recent"] = [
        {"id": g.get("id"), "issued": g.get("issuanceTime"), "name": g.get("productName")}
        for g in graph[:5]
    ]
    if not graph:
        out["note"] = "no CLI products at this location id; try the issuing office id"
        return out

    product = _get_json(f"https://api.weather.gov/products/{graph[0].get('id')}")
    text = product.get("productText") or ""
    out["issued"] = product.get("issuanceTime")
    out["text_chars"] = len(text)
    lines = text.splitlines()
    out["maximum_lines"] = [ln for ln in lines if "MAXIMUM" in ln.upper()][:4]
    out["excerpt"] = "\n".join(lines[:45])
    return out


def probe_nws(city: str, lat: float, lon: float,
              cli_locations: Sequence[str] = ()) -> dict[str, Any]:
    """Forecast shape, lead-time coverage, and whether observations are available.

    Three chained lookups, because the API is addressed by grid square rather
    than by coordinate: points -> gridpoint forecast, and points -> observation
    stations -> observations.
    """
    out: dict[str, Any] = {"city": city, "lat": lat, "lon": lon}

    points = _get_json(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
    props = points.get("properties", {})
    out["grid"] = {
        "office": props.get("gridId"),
        "x": props.get("gridX"),
        "y": props.get("gridY"),
        "forecast_url": props.get("forecast"),
        "hourly_url": props.get("forecastHourly"),
        "stations_url": props.get("observationStations"),
        "timezone": props.get("timeZone"),
    }

    if props.get("forecast"):
        forecast = _get_json(props["forecast"])
        periods = forecast.get("properties", {}).get("periods", [])
        daytime = [p for p in periods if p.get("isDaytime")]
        fprops = forecast.get("properties", {})
        out["forecast"] = {
            # The first run printed `updated: None` because this key is not
            # called that. Try the documented names in order and report which
            # one answered, so a future rename shows up as a change rather than
            # as a silent None.
            "updated": next(
                (fprops[k] for k in ("updateTime", "updated", "generatedAt") if fprops.get(k)),
                None,
            ),
            "updated_field_used": next(
                (k for k in ("updateTime", "updated", "generatedAt") if fprops.get(k)), None
            ),
            "n_periods": len(periods),
            "n_daytime_periods": len(daytime),
            "period_fields": sorted(periods[0].keys()) if periods else [],
            "sample_daytime_period": daytime[0] if daytime else None,
            # The model needs (value, unit, lead). All three must be present.
            "temperature_unit": daytime[0].get("temperatureUnit") if daytime else None,
            "daytime_highs": [
                {"name": p.get("name"), "start": p.get("startTime"),
                 "temperature": p.get("temperature")}
                for p in daytime
            ],
        }

    if props.get("observationStations"):
        stations = _get_json(props["observationStations"])
        features = stations.get("features", [])
        ids = [f.get("properties", {}).get("stationIdentifier") for f in features[:8]]
        out["stations"] = {"n": len(features), "nearest": ids}

        if ids and ids[0]:
            obs = _get_json(
                f"https://api.weather.gov/stations/{ids[0]}/observations?limit=48"
            )
            feats = obs.get("features", [])
            temps = [
                f.get("properties", {}).get("temperature", {}).get("value")
                for f in feats
            ]
            usable = [t for t in temps if t is not None]
            unit = (feats[0].get("properties", {}).get("temperature", {}).get("unitCode")
                    if feats else None)
            out["observations"] = {
                "station": ids[0],
                # Flagged, not converted. The station feed is Celsius while the
                # forecast is Fahrenheit, and more importantly a maximum taken
                # over hourly observations is NOT the number that settles the
                # contract - the climate report below is.
                "unit_matches_forecast": bool(unit and "degF" in unit),
                "n_returned": len(feats),
                "n_with_temperature": len(usable),
                "unit": (feats[0].get("properties", {}).get("temperature", {}).get("unitCode")
                         if feats else None),
                "oldest": feats[-1].get("properties", {}).get("timestamp") if feats else None,
                "newest": feats[0].get("properties", {}).get("timestamp") if feats else None,
                "sample_fields": sorted(feats[0].get("properties", {}).keys()) if feats else [],
            }

    # The settling product. Tried by station id first, then by issuing office,
    # because the CLI location id is one or the other depending on the site and
    # this probe exists to find out which.
    # Order matters: the ids parsed out of Kalshi's own settlement URLs first,
    # because those are the ones the contract actually names. Station id and
    # office are fallbacks and both returned HTTP 400 on the first run.
    station_id = out.get("observations", {}).get("station") or ""
    office = out.get("grid", {}).get("office") or ""
    ordered = list(cli_locations) + [c for c in (station_id, office) if c]
    for candidate in ordered:
        report = probe_climate_report(candidate, station_id)
        if report.get("n_recent_products"):
            out["climate_report"] = report
            break
        out.setdefault("climate_report_attempts", []).append(report)

    return out


# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kalshi-only", action="store_true")
    parser.add_argument("--nws-only", action="store_true")
    parser.add_argument("--city", default="nyc", choices=sorted(PROBE_CITIES))
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()

    report: dict[str, Any] = {}

    if not args.nws_only:
        print("=" * 72)
        print("KALSHI")
        print("=" * 72)
        try:
            kalshi = probe_kalshi(demo=args.demo)
            report["kalshi"] = kalshi
            print(f"series listed: {kalshi['n_series']}"
                  f"   TRUNCATED: {kalshi['series_listing_truncated']}")
            if kalshi["series_listing_truncated"]:
                print("   !! the cursor was still live at the page cap. "
                      "Anything absent below may simply not have been reached.")
            print(f"categories: {dict(sorted(kalshi['categories'].items()))}")

            print(f"\nweather series by the exchange's own category "
                  f"({len(kalshi['by_category'])}):")
            for row in kalshi["by_category"]:
                print(f"  {str(row['ticker']):<18} {str(row['frequency'] or ''):<10} "
                      f"{str(row['category'] or ''):<22} | {row['title']}")
                if row["settlement_sources"]:
                    print(f"      settles on: {row['settlement_sources']}")

            if kalshi["by_name_hint_only"]:
                print(f"\nmatched only by name hint, almost certainly false positives "
                      f"({len(kalshi['by_name_hint_only'])}):")
                for row in kalshi["by_name_hint_only"][:10]:
                    print(f"  {str(row['ticker']):<18} | {row['title']}")

            if not kalshi["by_category"]:
                print("\n!! the exchange lists no series it calls climate or weather.")
                print("   Either they are delisted or the category is named something")
                print("   else — read the `categories` line above. Do NOT guess a")
                print("   ticker format from this.")
            dt = kalshi.get("daily_temperature", {})
            print(f"\nDAILY temperature series: {dt.get('n', 0)}, by who settles them")
            for source, tickers in (dt.get("by_settlement_source") or {}).items():
                print(f"  {source:<36} {len(tickers):>4}")
            print(f"  CLI locations named in settlement URLs: "
                  f"{', '.join(dt.get('cli_locations') or []) or 'none'}")
            if dt.get("nws_settled"):
                print(f"  NWS-settled daily temperature series "
                      f"({len(dt['nws_settled'])}): {', '.join(dt['nws_settled'][:20])}")
            else:
                print("  !! NO daily temperature series settles on the NWS. The free-")
                print("     data route is closed and the domain choice needs revisiting.")

            print(f"\nbucket structure taken from: {kalshi['bucket_source_series']}")
            for tried in kalshi.get("bucket_source_tried", []):
                print(f"    tried {tried['ticker']}: "
                      f"{tried['open_events_with_markets']} open events with markets")
            if not kalshi.get("sample_event"):
                print("    !! no open event with markets on any candidate series, so the")
                print("       bucket structure is UNMEASURED. Not an absence of buckets.")

            event = kalshi.get("sample_event")
            if event:
                buckets = describe_buckets(event)
                report["buckets"] = buckets
                print(f"\nbucket structure of {event.get('event_ticker')}:")
                print(f"  markets in the event: {buckets['n_markets']}")
                print(f"  field population: {buckets['field_population']}")
                print(f"  markets with no floor_strike: {buckets['rows_missing_floor']}"
                      "  (expect 1 — the open bottom bucket)")
                print(f"  markets with no cap_strike:   {buckets['rows_missing_cap']}"
                      "  (expect 1 — the open top bucket)")
                for row in buckets["rows"][:20]:
                    print("   ", {k: v for k, v in row.items() if k != "rules_primary"})

                settlement = describe_settlement(kalshi["sample_market"])
                report["settlement"] = settlement
                print("\nsettlement rules:")
                print(f"  station ids mentioned: {settlement['mentions_station_id']}")
                print(f"  mentions degrees:      {settlement['mentions_degrees']}")
                print(f"  mentions rounding:     {settlement['mentions_nearest_whole']}")
                print(f"  mentions a maximum:    {settlement['mentions_maximum']}")
                for k, v in settlement["fields"].items():
                    print(f"  [{k}] {v[:400]}")
        except Exception as exc:  # noqa: BLE001 - a probe reports its own failure
            report["kalshi_error"] = repr(exc)
            print(f"!! kalshi probe failed: {exc!r}")

    if not args.kalshi_only:
        print()
        print("=" * 72)
        print("NATIONAL WEATHER SERVICE")
        print("=" * 72)
        try:
            lat, lon = PROBE_CITIES[args.city]
            cli_ids = (report.get("kalshi", {})
                       .get("daily_temperature", {}).get("cli_locations") or [])
            nws = probe_nws(args.city, lat, lon, cli_locations=cli_ids)
            report["nws"] = nws
            grid = nws.get("grid", {})
            print(f"{args.city}: grid {grid.get('office')} "
                  f"{grid.get('x')},{grid.get('y')}  tz={grid.get('timezone')}")

            forecast = nws.get("forecast", {})
            if forecast:
                print(f"forecast updated {forecast.get('updated')}, "
                      f"{forecast.get('n_daytime_periods')} daytime periods, "
                      f"unit={forecast.get('temperature_unit')}")
                for row in forecast.get("daytime_highs", []):
                    print(f"   {row['name']:<22} {row['start']}  {row['temperature']}")
                print("\n   NOTE: this is the CURRENT forecast. The error model needs "
                      "PAST\n   forecasts paired with what happened, which this endpoint "
                      "does not\n   serve. See the note printed at the end.")

            obs = nws.get("observations", {})
            if obs:
                print(f"\nobservations at {obs['station']}: "
                      f"{obs['n_with_temperature']}/{obs['n_returned']} carry a "
                      f"temperature, unit={obs['unit']}")
                print(f"   window: {obs['oldest']} .. {obs['newest']}")
                if not obs.get("unit_matches_forecast"):
                    print("   !! unit disagrees with the forecast (F). This feed is NOT")
                    print("      the settlement source in any case — see below.")

            cli = nws.get("climate_report")
            if cli:
                print(f"\nDaily Climate Report at {cli['location']} — "
                      f"THE SETTLEMENT SOURCE")
                print(f"   {cli.get('n_recent_products')} recent products, "
                      f"latest issued {cli.get('issued')}")
                for line in cli.get("maximum_lines", []):
                    print(f"   | {line}")
                print("\n   first lines of the product:")
                for line in (cli.get("excerpt") or "").splitlines()[:25]:
                    print(f"   | {line}")
            else:
                print("\n!! no Daily Climate Report retrieved. That product is what")
                print("   settles the contract, so without it there is no observed half")
                print("   to any (forecast, observed) pair.")
                for attempt in nws.get("climate_report_attempts", []):
                    print(f"   tried {attempt.get('location')}: "
                          f"{attempt.get('error') or attempt.get('note')}")
        except Exception as exc:  # noqa: BLE001
            report["nws_error"] = repr(exc)
            print(f"!! nws probe failed: {exc!r}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out_path}")

    print("""
WHAT IS STILL MISSING, AND IT IS THE WHOLE MODEL
------------------------------------------------
The forecast endpoint serves today's forecast. The error model needs the
distribution of (observed - forecast) by lead time, which requires PAST
forecasts paired with what actually happened. The NWS API does not serve its
own history, so there are two honest routes and one dishonest one:

  1. Start collecting now. One request per city per day, storing the forecast
     for each of the next seven days, and one request for the Daily Climate
     Report that settles the day just ended. After a month there are ~30 pairs
     per lead time per city, which is the minimum the model will accept, and it
     climbs from there.

     Note the day boundary: midnight to midnight local, EXCEPT under Daylight
     Saving Time, when the report runs 01:00 to 00:59 the following day. A
     collector that assumes calendar days is wrong for eight months a year, and
     wrong in a way that looks like forecast error rather than like a bug.

  2. Pull an archive. NOAA publishes model output on AWS Open Data with no key
     (the National Blend of Models bucket), which back-fills the forecast half
     immediately, and daily observed maxima come from the same station records.
     Heavier to wire up, but it means a fitted model this month rather than
     next.

  3. Assume a spread — "forecast errors are about 3F" — and quote prices from
     it. This produces a working system, a plausible backtest, and no way to
     know it is wrong. `ErrorModel.fit` raises `UncalibratedModel` rather than
     accept a hand-supplied sigma, specifically so that route stays closed.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
