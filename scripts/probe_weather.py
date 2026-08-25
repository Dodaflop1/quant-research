#!/usr/bin/env python3
"""Measure the two interfaces the temperature model needs, before building on them.

This script asks questions. It does not fit anything, trade anything, or write
a result. It exists because four separate failures in this project came from
building on an assumed payload shape: guessed `volume` where the API returns
`volume_fp`, guessed `yes_price` where it returns `yes_price_dollars`, an
alphabetical slice mistaken for a sample. The model in
`src/quant/kalshi/models/temperature.py` is written against *no* assumed field
names, and this is what supplies the real ones.

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

Usage
-----
    python scripts/probe_weather.py                  # everything
    python scripts/probe_weather.py --kalshi-only
    python scripts/probe_weather.py --nws-only
    python scripts/probe_weather.py --station KNYC   # skip the lookup
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
from typing import Any, Optional

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


def probe_kalshi(demo: bool = False, max_pages: int = 12) -> dict[str, Any]:
    """Enumerate open events and report every temperature-looking series."""
    from dotenv import load_dotenv
    from quant.common.api.kalshi import KalshiClient

    if Path(".env").exists():
        load_dotenv(".env")
    # 2.0 rather than the client default of 8. The default earned 90 minutes of
    # HTTP 429 on this API once already.
    client = KalshiClient.from_env(demo=demo, rate_per_sec=2.0)

    out: dict[str, Any] = {"series": {}, "sample_market": None, "sample_event": None}
    cursor: Optional[str] = None
    seen_events = 0

    for _ in range(max_pages):
        params: dict[str, Any] = {
            "limit": 200,
            "status": "open",
            "with_nested_markets": "true",
        }
        if cursor:
            params["cursor"] = cursor
        body = client.get("/events", params).payload
        events = body.get("events") or []
        seen_events += len(events)

        for event in events:
            ticker = str(event.get("event_ticker") or "")
            series = ticker.split("-")[0]
            if not any(hint in series.upper() for hint in TEMPERATURE_HINTS):
                continue
            markets = event.get("markets") or []
            entry = out["series"].setdefault(
                series,
                {"events": 0, "markets": 0, "example_event": ticker,
                 "mutually_exclusive": event.get("mutually_exclusive"),
                 "title": event.get("title")},
            )
            entry["events"] += 1
            entry["markets"] += len(markets)

            if out["sample_market"] is None and markets:
                out["sample_event"] = event
                out["sample_market"] = markets[0]

        cursor = body.get("cursor") or None
        if not cursor:
            break

    out["events_scanned"] = seen_events
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


def probe_nws(city: str, lat: float, lon: float) -> dict[str, Any]:
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
        out["forecast"] = {
            "updated": forecast.get("properties", {}).get("updated"),
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
            out["observations"] = {
                "station": ids[0],
                "n_returned": len(feats),
                "n_with_temperature": len(usable),
                "unit": (feats[0].get("properties", {}).get("temperature", {}).get("unitCode")
                         if feats else None),
                "oldest": feats[-1].get("properties", {}).get("timestamp") if feats else None,
                "newest": feats[0].get("properties", {}).get("timestamp") if feats else None,
                "sample_fields": sorted(feats[0].get("properties", {}).keys()) if feats else [],
            }

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
            print(f"events scanned: {kalshi['events_scanned']}")
            if not kalshi["series"]:
                print("\n!! no series matched any of", TEMPERATURE_HINTS)
                print("   The naming is different. Widen the hints and rerun —")
                print("   do NOT guess the ticker format from this.")
            for name, info in sorted(kalshi["series"].items()):
                print(f"  {name:<20} events={info['events']:<4} "
                      f"markets={info['markets']:<5} "
                      f"mutually_exclusive={info['mutually_exclusive']} "
                      f"| {info['title']}")

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
            nws = probe_nws(args.city, lat, lon)
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
     for each of the next seven days, and one request for the observed high.
     After a month there are ~30 pairs per lead time per city, which is the
     minimum the model will accept, and it climbs from there.

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
