"""Backfill Kalshi trade prints.

Unlike order books, trades *are* recoverable after the fact: Kalshi serves the
last few months from ``/markets/trades`` and everything older from
``/historical/trades``. That single fact is what makes a point-process study of
Kalshi order flow viable on a short timeline - the dataset can be downloaded
rather than accumulated over six weeks.

Scope decides whether it is an afternoon or a fortnight. Measured on
2026-08-24, the whole-exchange tape runs at about 3.56 million trades per nine
hours - roughly 2.8 GB of raw pages per day, so 90 days is ~250 GB. Restricted
to the 150-market collected universe it is a few hundred megabytes. Fetch the
universe unless the analysis genuinely needs every market.

The design still borrows the collector's discipline, for different reasons:

* **Raw pages are written verbatim before parsing.** Re-downloading months of
  history to fix a parser bug is slow and rude to the API even though it is
  possible.
* **Work is chunked into date windows and checkpointed.** A backfill that dies
  part way resumes where it stopped. A window is either complete or re-fetched
  whole, which keeps the checkpoint trivially idempotent. Window size is a
  scope decision - see :func:`windows`.
* **Both endpoints are queried over every window.** The live/historical cutoff
  rolls daily and is not published, so the seam is covered by overlap and
  deduplication on ``trade_id`` rather than by a hardcoded date.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from quant.common.api.kalshi import KalshiClient, parse_trade
from quant.common.db.schema import Trade
from quant.ingest.kalshi_collector import RawWriter

log = logging.getLogger(__name__)


@dataclass
class BackfillStats:
    pages: int = 0
    trades_seen: int = 0
    days_done: int = 0
    days_skipped: int = 0
    errors: int = 0
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def summary(self) -> str:
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        return (
            f"{self.pages} pages, {self.trades_seen} trade records, "
            f"{self.days_done} days fetched, {self.days_skipped} already done, "
            f"{self.errors} errors over {elapsed / 60:.1f} min"
        )


class Checkpoint:
    """Records which (ticker, day, endpoint) windows have completed.

    Kept as a flat JSON set rather than a database because the only operations
    needed are membership and append, and a corrupted checkpoint should cost a
    re-download rather than a debugging session.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._done: set[str] = set()
        if self.path.exists():
            try:
                self._done = set(json.loads(self.path.read_text()))
                log.info("resuming: %d windows already complete", len(self._done))
            except (json.JSONDecodeError, TypeError, ValueError):
                log.warning("%s is unreadable, starting fresh", self.path)

    @staticmethod
    def key(ticker: Optional[str], start: date, end: Optional[date] = None) -> str:
        # The window is part of the key, not just its start. Re-running with a
        # different --chunk-days must not silently inherit completions recorded
        # under a different window size and skip data it never fetched.
        end = end or start
        return f"{ticker or '*'}|{start.isoformat()}|{end.isoformat()}"

    def done(self, ticker: Optional[str], start: date, end: Optional[date] = None) -> bool:
        return self.key(ticker, start, end) in self._done

    def mark(self, ticker: Optional[str], start: date, end: Optional[date] = None) -> None:
        self._done.add(self.key(ticker, start, end))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(self._done)))
        tmp.replace(self.path)


def days_between(start: date, end: date) -> Iterator[date]:
    """Yield each UTC day in ``[start, end]``, newest first.

    Newest first on purpose. If the backfill is interrupted the data you have is
    the most recent, which is the part every downstream analysis wants and the
    part most likely to overlap the live collector already running.
    """
    day = end
    while day >= start:
        yield day
        day -= timedelta(days=1)


def windows(start: date, end: date, chunk_days: int = 1) -> Iterator[tuple[date, date]]:
    """Yield ``(from, to)`` inclusive date windows, newest first.

    Window size is the difference between a feasible backfill and an infeasible
    one, and it depends entirely on scope.

    Measured on 2026-08-24: the **whole-exchange** tape ran at roughly 3.56
    million trades per nine hours, about 2.8 GB of raw pages per day. Ninety
    days of that is ~250 GB and days of wall clock. Day-sized windows are right
    there, because a single day is already a long unit of work and losing one to
    an interruption hurts.

    A **pinned 150-market universe** is a different problem: most tickers have a
    handful of trades a day, so a day-sized window costs a request and returns
    almost nothing. 150 tickers x 90 days x 2 endpoints is 27,000 requests to
    move a few hundred megabytes. One window spanning the whole range per ticker
    does the same job in 300.
    """
    if chunk_days < 1:
        raise ValueError("chunk_days must be at least 1")
    window_end = end
    while window_end >= start:
        window_start = max(start, window_end - timedelta(days=chunk_days - 1))
        yield window_start, window_end
        window_end = window_start - timedelta(days=1)


def _unix(day: date, end_of_day: bool = False) -> int:
    moment = datetime.combine(
        day, datetime.min.time(), tzinfo=timezone.utc
    ) + (timedelta(days=1) if end_of_day else timedelta())
    return int(moment.timestamp())


class TradeBackfill:
    def __init__(
        self,
        client: KalshiClient,
        out_dir: Path,
        page_limit: int = 1000,
    ):
        self.client = client
        self.out_dir = Path(out_dir)
        self.page_limit = page_limit
        self.writer = RawWriter(self.out_dir / "raw", "kalshi_trades")
        self.checkpoint = Checkpoint(self.out_dir / "trade_backfill_checkpoint.json")
        self.stats = BackfillStats()

    def _fetch_day(
        self, ticker: Optional[str], day: date, until: Optional[date] = None
    ) -> int:
        until = until or day
        min_ts, max_ts = _unix(day), _unix(until, end_of_day=True)
        count = 0
        for raw in self.client.iter_all_trades(
            ticker=ticker, min_ts=min_ts, max_ts=max_ts, limit=self.page_limit
        ):
            trades = (raw.payload or {}).get("trades") or []
            self.writer.write(
                {
                    "kind": "trades_page",
                    "ticker": ticker,
                    "day": day.isoformat(),
                    "until": until.isoformat(),
                    "path": raw.path,
                    "requested_at": raw.requested_at.isoformat(),
                    "received_at": raw.received_at.isoformat(),
                    "payload": raw.payload,
                }
            )
            self.stats.pages += 1
            count += len(trades)
        self.stats.trades_seen += count
        return count

    def run(
        self,
        start: date,
        end: date,
        tickers: Optional[list[str]] = None,
        chunk_days: int = 1,
    ) -> BackfillStats:
        """Fetch ``[start, end]`` for each ticker, or market-wide.

        ``tickers=None`` fetches the whole exchange tape. That is the
        assumption-free choice, and it is also 2.8 GB per day: measured
        exchange-wide throughput on 2026-08-24 was 3.56 million trades in nine
        hours. Ninety days is roughly 250 GB. Pass the pinned universe unless
        the whole tape is genuinely what the analysis needs, and see
        :func:`windows` for why ``chunk_days`` should then be large.
        """
        targets: list[Optional[str]] = list(tickers) if tickers else [None]
        spans = list(windows(start, end, chunk_days))
        total = len(targets) * len(spans)

        if tickers is None and chunk_days == 1:
            span_days = (end - start).days + 1
            log.warning(
                "fetching the WHOLE EXCHANGE tape for %d day(s). Measured rate "
                "is about 2.8 GB/day, so expect roughly %.0f GB and many hours. "
                "Pass --tickers-file to restrict this to the collected universe.",
                span_days,
                span_days * 2.8,
            )

        log.info(
            "backfilling %s to %s for %s in %d window(s) of %d day(s) "
            "(%d fetches) into %s",
            start,
            end,
            f"{len(targets)} tickers" if tickers else "all markets",
            len(spans),
            chunk_days,
            total,
            self.out_dir,
        )

        try:
            for ticker in targets:
                for win_start, win_end in spans:
                    if self.checkpoint.done(ticker, win_start, win_end):
                        self.stats.days_skipped += 1
                        continue
                    try:
                        count = self._fetch_day(ticker, win_start, win_end)
                    except Exception as exc:  # noqa: BLE001
                        # A window that fails is left unmarked and retried on the
                        # next run. Aborting the whole backfill because one
                        # window 500s would be a worse trade.
                        self.stats.errors += 1
                        log.warning(
                            "%s %s..%s failed: %s: %s",
                            ticker or "all",
                            win_start,
                            win_end,
                            type(exc).__name__,
                            exc,
                            exc_info=True,
                        )
                        continue
                    self.checkpoint.mark(ticker, win_start, win_end)
                    self.stats.days_done += 1
                    log.info(
                        "%s %s..%s: %d trade records (%d/%d)",
                        ticker or "all",
                        win_start,
                        win_end,
                        count,
                        self.stats.days_done + self.stats.days_skipped,
                        total,
                    )
        except BaseException as exc:  # noqa: BLE001
            log.critical(
                "backfill exiting on %s: %s", type(exc).__name__, exc, exc_info=True
            )
            raise
        finally:
            self.writer.close()
            log.info("stopped: %s", self.stats.summary())
        return self.stats


def parse_trade_files(paths: Iterable[Path]) -> Iterator[Trade]:
    """Parse raw trade pages into :class:`Trade`, deduplicated by ``trade_id``.

    Deduplication is required, not defensive: every window is fetched from both
    the live and historical endpoints, so trades near the rolling cutoff arrive
    twice by design. Counting them twice would inflate exactly the arrival rate
    the Hawkes fit is trying to measure.
    """
    seen: set[str] = set()
    failures: Counter[str] = Counter()
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    failures["unparseable_json"] += 1
                    continue
                if record.get("kind") != "trades_page":
                    continue
                for entry in (record.get("payload") or {}).get("trades") or []:
                    trade_id = entry.get("trade_id")
                    if trade_id is not None:
                        if trade_id in seen:
                            failures["duplicate"] += 1
                            continue
                        seen.add(trade_id)
                    try:
                        yield parse_trade(entry)
                    except Exception as exc:  # noqa: BLE001
                        failures[type(exc).__name__] += 1
                        if failures[type(exc).__name__] <= 3:
                            log.warning(
                                "%s:%d could not parse trade %s: %s",
                                path.name,
                                lineno,
                                trade_id,
                                exc,
                            )
    if failures:
        # A silent skip rate is indistinguishable from a thin tape. Report it.
        log.info("parse tally: %s", dict(failures))
