"""Kalshi order book collector.

Order books cannot be backfilled. The dataset starts the moment this first runs,
so the design priority is not losing anything, ahead of convenience or speed.

Three choices follow from that:

* **Raw payloads are the source of truth.** Every response is appended verbatim
  to JSONL with its receive time, before any parsing. A parsing bug then costs
  a reparse rather than a day of data.
* **Append-only plain text, flushed and fsynced.** No format that can be
  corrupted by an ungraceful kill halfway through a write. Compression happens
  later, on days that are already complete.
* **A failed market does not stop the loop.** One bad ticker must not take down
  a collector that is the only copy of everything else.

Parsing into :class:`OrderBookSnapshot` happens offline, in ``parse_raw_file``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from quant.common.api.kalshi import KalshiAPIError, KalshiClient, parse_orderbook
from quant.common.db.schema import OrderBookSnapshot

log = logging.getLogger(__name__)


@dataclass
class CollectorStats:
    polls: int = 0
    snapshots: int = 0
    errors: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def summary(self) -> str:
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        return (
            f"{self.polls} polls, {self.snapshots} snapshots, {self.errors} errors "
            f"over {elapsed / 60:.1f} min"
        )


class RawWriter:
    """Append-only JSONL writer with daily rotation by UTC date."""

    def __init__(self, out_dir: Path, prefix: str, fsync_every: int = 50):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.fsync_every = fsync_every
        self._day: Optional[date] = None
        self._fh = None
        self._since_sync = 0

    def _path_for(self, day: date) -> Path:
        return self.out_dir / f"{self.prefix}_{day.isoformat()}.jsonl"

    def _rotate(self, day: date) -> None:
        if self._fh is not None:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
        self._fh = open(self._path_for(day), "a", encoding="utf-8")
        self._day = day
        log.info("writing to %s", self._path_for(day))

    def write(self, record: dict) -> None:
        today = datetime.now(timezone.utc).date()
        if self._fh is None or today != self._day:
            self._rotate(today)
        assert self._fh is not None
        self._fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._since_sync += 1
        if self._since_sync >= self.fsync_every:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._since_sync = 0

    def close(self) -> None:
        if self._fh is not None:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
            self._fh = None


def discover_markets(
    client: KalshiClient,
    mutually_exclusive_only: bool = True,
    min_markets: int = 2,
    max_events: Optional[int] = None,
) -> list[dict]:
    """Find open events and their markets.

    Defaults to mutually exclusive events with at least two markets, which is
    exactly the family the bucket-sum check applies to: their YES prices must
    sum to 100c before friction.
    """
    found: list[dict] = []
    for event in client.iter_events(status="open", with_nested_markets=True):
        markets = event.get("markets") or []
        if mutually_exclusive_only and not event.get("mutually_exclusive"):
            continue
        if len(markets) < min_markets:
            continue
        found.append(
            {
                "event_ticker": event.get("event_ticker"),
                "series_ticker": event.get("series_ticker"),
                "title": event.get("title"),
                "mutually_exclusive": bool(event.get("mutually_exclusive")),
                "market_tickers": [m.get("ticker") for m in markets if m.get("ticker")],
            }
        )
        if max_events is not None and len(found) >= max_events:
            break
    return found


class KalshiCollector:
    def __init__(
        self,
        client: KalshiClient,
        out_dir: Path,
        interval_sec: float = 30.0,
        depth: int = 0,
        metadata_every_sec: float = 3600.0,
    ):
        self.client = client
        self.out_dir = Path(out_dir)
        self.interval_sec = interval_sec
        self.depth = depth
        self.metadata_every_sec = metadata_every_sec
        self.books = RawWriter(self.out_dir / "raw", "kalshi_orderbook")
        self.meta = RawWriter(self.out_dir / "raw", "kalshi_metadata")
        self.stats = CollectorStats()
        self._stop = False
        self._meta_digest: Optional[str] = None

    def request_stop(self, *_args) -> None:
        log.info("stop requested, finishing current cycle")
        self._stop = True

    def _write_metadata_if_changed(self, events: list[dict]) -> bool:
        """Write the event catalogue only when it actually differs.

        Rewriting it verbatim every hour cost 23MB in the first 13 hours of
        live collection - roughly 1.8GB over a six-week run - for a catalogue
        that changes a few times a day. Hashing it first keeps the record of
        *when* it changed, which is the part with research value, without the
        repetition.
        """
        payload = json.dumps(events, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        if digest == self._meta_digest:
            return False
        self._meta_digest = digest
        self.meta.write(
            {
                "kind": "events",
                "at": datetime.now(timezone.utc).isoformat(),
                "digest": digest,
                "events": events,
            }
        )
        log.info("event catalogue changed (%d events)", len(events))
        return True

    def _poll_once(self, tickers: list[str]) -> None:
        for ticker in tickers:
            if self._stop:
                return
            try:
                raw = self.client.get_orderbook_raw(ticker, depth=self.depth)
            except Exception as exc:  # noqa: BLE001
                # Deliberately catch everything. This process holds the only
                # copy of data that cannot be re-fetched, so dying on an
                # unforeseen exception is the worst available outcome - worse
                # than any wrong answer it might record.
                #
                # The narrow `except KalshiAPIError` this replaces let a live
                # run die silently after 90 minutes, having produced no log of
                # why. Whatever escaped was not the failure mode anticipated,
                # which is precisely the argument for not anticipating.
                self.stats.errors += 1
                log.warning(
                    "orderbook failed for %s: %s: %s",
                    ticker,
                    type(exc).__name__,
                    exc,
                    exc_info=not isinstance(exc, KalshiAPIError),
                )
                self.books.write(
                    {
                        "kind": "error",
                        "ticker": ticker,
                        "at": datetime.now(timezone.utc).isoformat(),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                continue

            self.books.write(
                {
                    "kind": "orderbook",
                    "ticker": ticker,
                    "requested_at": raw.requested_at.isoformat(),
                    "received_at": raw.received_at.isoformat(),
                    "payload": raw.payload,
                }
            )
            self.stats.snapshots += 1

    def run(
        self,
        tickers: list[str],
        events: Optional[list[dict]] = None,
        max_cycles: Optional[int] = None,
    ) -> CollectorStats:
        if not tickers:
            raise ValueError("no tickers to collect")

        log.info(
            "collecting %d markets every %.0fs into %s",
            len(tickers),
            self.interval_sec,
            self.out_dir,
        )
        if events:
            self._write_metadata_if_changed(events)

        last_meta = time.monotonic()
        cycles = 0
        try:
            while not self._stop:
                cycle_start = time.monotonic()
                before = self.stats.snapshots
                self._poll_once(tickers)
                self.stats.polls += 1
                cycles += 1
                elapsed = time.monotonic() - cycle_start

                # A heartbeat every cycle. Without it a healthy collector and a
                # hung one look identical for hours, which is untenable in a
                # process meant to run unattended for weeks.
                log.info(
                    "cycle %d: %d snapshots in %.1fs (%d total, %d errors)",
                    cycles,
                    self.stats.snapshots - before,
                    elapsed,
                    self.stats.snapshots,
                    self.stats.errors,
                )

                if time.monotonic() - last_meta >= self.metadata_every_sec:
                    try:
                        refreshed = discover_markets(self.client, max_events=None)
                        self._write_metadata_if_changed(refreshed)
                    except Exception as exc:  # noqa: BLE001
                        self.stats.errors += 1
                        log.warning("metadata refresh failed: %s", exc, exc_info=True)
                    last_meta = time.monotonic()

                if max_cycles is not None and cycles >= max_cycles:
                    break

                elapsed = time.monotonic() - cycle_start
                if elapsed > self.interval_sec:
                    log.warning(
                        "cycle took %.1fs, longer than the %.0fs interval; "
                        "reduce the market count or raise --interval",
                        elapsed,
                        self.interval_sec,
                    )
                else:
                    time.sleep(self.interval_sec - elapsed)
        except BaseException as exc:  # noqa: BLE001
            # Whatever ends this run, say so before going. The previous version
            # exited without a word, leaving no evidence of why.
            log.critical(
                "collector exiting on %s: %s", type(exc).__name__, exc, exc_info=True
            )
            raise
        finally:
            self.books.close()
            self.meta.close()
            log.info("stopped: %s", self.stats.summary())
        return self.stats


def parse_raw_file(path: Path) -> Iterator[OrderBookSnapshot]:
    """Parse a raw JSONL file into snapshots, offline.

    Malformed or unparseable lines are logged and skipped rather than raised,
    so one bad record cannot block access to the rest of the day.
    """
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                log.warning("%s:%d unparseable JSON, skipped", path.name, lineno)
                continue
            if record.get("kind") != "orderbook":
                continue
            try:
                yield parse_orderbook(
                    record["payload"],
                    record["ticker"],
                    datetime.fromisoformat(record["received_at"]),
                )
            except Exception as exc:  # noqa: BLE001 - one bad book must not stop the file
                log.warning(
                    "%s:%d could not parse book for %s: %s",
                    path.name,
                    lineno,
                    record.get("ticker"),
                    exc,
                )


def install_signal_handlers(collector: KalshiCollector) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, collector.request_stop)
