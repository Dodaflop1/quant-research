"""Tests for the collector's durability properties.

The collector holds the only copy of data that cannot be re-fetched, so these
check the loss-prevention behaviour specifically: appends survive reopening,
one bad market does not stop a cycle, and one bad line does not block a file.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quant.common.api.kalshi import KalshiAPIError
from quant.ingest.kalshi_collector import (
    KalshiCollector,
    RawWriter,
    discover_markets,
    parse_raw_file,
)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Writer durability
# ---------------------------------------------------------------------------


def test_writer_appends_across_reopen(tmp_path: Path):
    """A restarted collector must not truncate the day it is resuming."""
    for i in range(3):
        w = RawWriter(tmp_path, "book")
        w.write({"kind": "orderbook", "n": i})
        w.close()

    files = list(tmp_path.glob("book_*.jsonl"))
    assert len(files) == 1
    assert [json.loads(line)["n"] for line in files[0].read_text().splitlines()] == [0, 1, 2]


def test_writer_emits_one_json_object_per_line(tmp_path: Path):
    w = RawWriter(tmp_path, "book")
    w.write({"a": 1, "nested": {"b": [1, 2]}})
    w.write({"a": 2})
    w.close()
    lines = next(tmp_path.glob("book_*.jsonl")).read_text().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line) for line in lines)


def test_writer_file_named_by_utc_date(tmp_path: Path):
    w = RawWriter(tmp_path, "book")
    w.write({"x": 1})
    w.close()
    name = next(tmp_path.glob("book_*.jsonl")).name
    assert datetime.now(UTC).date().isoformat() in name


def test_partial_final_line_does_not_block_the_rest(tmp_path: Path):
    """Simulates a kill mid-write: the last line is truncated."""
    path = tmp_path / "book_2026-08-23.jsonl"
    good = {"kind": "orderbook", "ticker": "X", "received_at": "2026-08-23T12:00:00+00:00",
            "payload": {"orderbook_fp": {"yes_dollars": [["0.5000", "1.00"]], "no_dollars": []}}}
    path.write_text(json.dumps(good) + "\n" + json.dumps(good)[:40])

    books = list(parse_raw_file(path))
    assert len(books) == 1
    assert books[0].best_yes_bid == 50.0


def test_unparseable_payload_skipped_not_raised(tmp_path: Path):
    path = tmp_path / "book_2026-08-23.jsonl"
    rows = [
        {"kind": "orderbook", "ticker": "X", "received_at": "2026-08-23T12:00:00+00:00",
         "payload": {"orderbook_fp": {"yes_dollars": [["oops", "1.00"]], "no_dollars": []}}},
        {"kind": "orderbook", "ticker": "Y", "received_at": "2026-08-23T12:00:01+00:00",
         "payload": {"orderbook_fp": {"yes_dollars": [["0.4000", "2.00"]], "no_dollars": []}}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows))
    books = list(parse_raw_file(path))
    assert [b.contract_id for b in books] == ["Y"]


def test_error_records_are_not_parsed_as_books(tmp_path: Path):
    path = tmp_path / "book_2026-08-23.jsonl"
    path.write_text(json.dumps({"kind": "error", "ticker": "X", "error": "boom"}) + "\n")
    assert list(parse_raw_file(path)) == []


# ---------------------------------------------------------------------------
# Loop resilience
# ---------------------------------------------------------------------------


class _FakeClient:
    """Stands in for KalshiClient; fails on one ticker."""

    def __init__(self, failing: set[str] | None = None):
        self.failing = failing or set()
        self.calls: list[str] = []

    def get_orderbook_raw(self, ticker: str, depth: int = 0):
        self.calls.append(ticker)
        if ticker in self.failing:
            raise KalshiAPIError(500, "boom", f"/markets/{ticker}/orderbook")

        from quant.common.api.kalshi import RawResponse

        now = datetime.now(UTC)
        return RawResponse(
            path=f"/markets/{ticker}/orderbook",
            params={},
            requested_at=now,
            received_at=now,
            payload={"orderbook_fp": {"yes_dollars": [["0.5000", "10.00"]], "no_dollars": []}},
        )

    def iter_events(self, **kwargs):
        return iter(())


def test_one_failing_market_does_not_stop_the_cycle(tmp_path: Path):
    client = _FakeClient(failing={"BAD"})
    collector = KalshiCollector(client, tmp_path, interval_sec=0.0)
    stats = collector.run(["A", "BAD", "B"], max_cycles=1)

    assert client.calls == ["A", "BAD", "B"]
    assert stats.snapshots == 2
    assert stats.errors == 1


def test_failure_is_recorded_not_silently_dropped(tmp_path: Path):
    collector = KalshiCollector(_FakeClient(failing={"BAD"}), tmp_path, interval_sec=0.0)
    collector.run(["BAD"], max_cycles=1)

    lines = [json.loads(x) for x in
             next((tmp_path / "raw").glob("kalshi_orderbook_*.jsonl")).read_text().splitlines()]
    assert [r["kind"] for r in lines] == ["error"]
    assert "boom" in lines[0]["error"]


def test_raw_payload_preserved_verbatim(tmp_path: Path):
    """Reparsing must be possible, so the payload is stored unmodified."""
    collector = KalshiCollector(_FakeClient(), tmp_path, interval_sec=0.0)
    collector.run(["A"], max_cycles=1)

    record = json.loads(
        next((tmp_path / "raw").glob("kalshi_orderbook_*.jsonl")).read_text().splitlines()[0]
    )
    assert record["payload"]["orderbook_fp"]["yes_dollars"] == [["0.5000", "10.00"]]
    assert record["requested_at"] and record["received_at"]


def test_collected_file_round_trips_to_snapshots(tmp_path: Path):
    collector = KalshiCollector(_FakeClient(), tmp_path, interval_sec=0.0)
    collector.run(["A", "B"], max_cycles=1)

    path = next((tmp_path / "raw").glob("kalshi_orderbook_*.jsonl"))
    books = list(parse_raw_file(path))
    assert [b.contract_id for b in books] == ["A", "B"]
    assert all(b.best_yes_bid == 50.0 for b in books)


def test_empty_ticker_list_rejected(tmp_path: Path):
    collector = KalshiCollector(_FakeClient(), tmp_path, interval_sec=0.0)
    with pytest.raises(ValueError):
        collector.run([])


def test_stop_request_ends_the_loop(tmp_path: Path):
    collector = KalshiCollector(_FakeClient(), tmp_path, interval_sec=0.0)
    collector.request_stop()
    stats = collector.run(["A"], max_cycles=None)
    assert stats.snapshots == 0


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class _EventClient:
    def __init__(self, events):
        self._events = events

    def iter_events(self, status="open", with_nested_markets=True):
        return iter(self._events)


def test_discovery_keeps_only_mutually_exclusive_families():
    client = _EventClient([
        {"event_ticker": "ME", "mutually_exclusive": True,
         "markets": [{"ticker": "A"}, {"ticker": "B"}]},
        {"event_ticker": "NOT-ME", "mutually_exclusive": False,
         "markets": [{"ticker": "C"}, {"ticker": "D"}]},
    ])
    found = discover_markets(client)
    assert [e["event_ticker"] for e in found] == ["ME"]


def test_discovery_skips_single_market_events():
    client = _EventClient([
        {"event_ticker": "SOLO", "mutually_exclusive": True, "markets": [{"ticker": "A"}]},
    ])
    assert discover_markets(client) == []


def test_discovery_can_include_non_exclusive_events():
    client = _EventClient([
        {"event_ticker": "NOT-ME", "mutually_exclusive": False,
         "markets": [{"ticker": "C"}, {"ticker": "D"}]},
    ])
    assert len(discover_markets(client, mutually_exclusive_only=False)) == 1


def test_discovery_respects_max_events():
    client = _EventClient([
        {"event_ticker": f"E{i}", "mutually_exclusive": True,
         "markets": [{"ticker": "A"}, {"ticker": "B"}]}
        for i in range(10)
    ])
    assert len(discover_markets(client, max_events=3)) == 3


# ---------------------------------------------------------------------------
# Running unattended
# ---------------------------------------------------------------------------


def test_each_cycle_logs_a_heartbeat(tmp_path: Path, caplog):
    """Silence for hours is indistinguishable from a hang.

    The first live run printed three startup lines and then nothing for 13
    hours while working perfectly.
    """
    import logging as _logging
    import re

    collector = KalshiCollector(_FakeClient(), tmp_path, interval_sec=0.0)
    with caplog.at_level(_logging.INFO, logger="quant.ingest.kalshi_collector"):
        collector.run(["A", "B"], max_cycles=2)

    # Anchored: the pacing warning also opens with "cycle ".
    beats = [r for r in caplog.records if re.match(r"^cycle \d+:", r.getMessage())]
    assert len(beats) == 2
    assert "snapshots" in beats[0].getMessage()


def test_unchanged_event_catalogue_written_once(tmp_path: Path):
    """Rewriting it hourly cost 23MB in 13 hours for data that rarely changes."""
    collector = KalshiCollector(_FakeClient(), tmp_path, interval_sec=0.0)
    events = [{"event_ticker": "E1", "market_tickers": ["A"]}]

    assert collector._write_metadata_if_changed(events) is True
    assert collector._write_metadata_if_changed(events) is False
    assert collector._write_metadata_if_changed(events) is False
    collector.meta.close()

    path = next((tmp_path / "raw").glob("kalshi_metadata_*.jsonl"))
    assert len(path.read_text().splitlines()) == 1


def test_changed_catalogue_is_recorded(tmp_path: Path):
    """When it does change, that moment is the part worth keeping."""
    collector = KalshiCollector(_FakeClient(), tmp_path, interval_sec=0.0)
    assert collector._write_metadata_if_changed([{"event_ticker": "E1"}]) is True
    assert collector._write_metadata_if_changed([{"event_ticker": "E2"}]) is True
    collector.meta.close()

    path = next((tmp_path / "raw").glob("kalshi_metadata_*.jsonl"))
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["digest"] != rows[1]["digest"]


def test_catalogue_digest_ignores_key_order(tmp_path: Path):
    collector = KalshiCollector(_FakeClient(), tmp_path, interval_sec=0.0)
    assert collector._write_metadata_if_changed([{"a": 1, "b": 2}]) is True
    assert collector._write_metadata_if_changed([{"b": 2, "a": 1}]) is False


# ---------------------------------------------------------------------------
# Surviving the unexpected
#
# A live run died silently after 90 minutes. The poll loop caught only
# KalshiAPIError, so whatever actually escaped was by definition the failure
# mode nobody anticipated - which is the argument for not trying to.
# ---------------------------------------------------------------------------


class _ExplodingClient(_FakeClient):
    """Raises something the collector was never taught about."""

    def __init__(self, exc: Exception, on: str):
        super().__init__()
        self.exc = exc
        self.on = on

    def get_orderbook_raw(self, ticker: str, depth: int = 0):
        # The parent records the call itself; only the failing path needs it here.
        if ticker == self.on:
            self.calls.append(ticker)
            raise self.exc
        return super().get_orderbook_raw(ticker, depth)


@pytest.mark.parametrize(
    "exc",
    [
        KeyError("payload"),
        ValueError("malformed price"),
        RuntimeError("connection reset"),
        MemoryError(),
    ],
)
def test_unexpected_exception_does_not_kill_the_collector(tmp_path: Path, exc):
    client = _ExplodingClient(exc, on="BAD")
    collector = KalshiCollector(client, tmp_path, interval_sec=0.0)
    stats = collector.run(["A", "BAD", "B"], max_cycles=1)

    assert client.calls == ["A", "BAD", "B"]
    assert stats.snapshots == 2
    assert stats.errors == 1


def test_unexpected_exception_is_recorded_with_its_type(tmp_path: Path):
    """A post-mortem needs to know what escaped, not just that something did."""
    collector = KalshiCollector(
        _ExplodingClient(KeyError("orderbook_fp"), on="BAD"), tmp_path, interval_sec=0.0
    )
    collector.run(["BAD"], max_cycles=1)

    row = json.loads(
        next((tmp_path / "raw").glob("kalshi_orderbook_*.jsonl")).read_text().splitlines()[0]
    )
    assert row["kind"] == "error"
    assert row["error_type"] == "KeyError"


def test_keyboard_interrupt_still_closes_files(tmp_path: Path):
    """Ctrl+C must flush, not lose the tail of the day."""

    class _Interrupting(_FakeClient):
        def get_orderbook_raw(self, ticker, depth=0):
            raise KeyboardInterrupt

    collector = KalshiCollector(_Interrupting(), tmp_path, interval_sec=0.0)
    with pytest.raises(KeyboardInterrupt):
        collector.run(["A"], max_cycles=1)
    assert collector.books._fh is None


def test_exit_is_announced_before_going(tmp_path: Path, caplog):
    import logging as _logging

    class _Interrupting(_FakeClient):
        def get_orderbook_raw(self, ticker, depth=0):
            raise KeyboardInterrupt

    collector = KalshiCollector(_Interrupting(), tmp_path, interval_sec=0.0)
    with caplog.at_level(_logging.CRITICAL, logger="quant.ingest.kalshi_collector"):
        with pytest.raises(KeyboardInterrupt):
            collector.run(["A"], max_cycles=1)

    assert any("exiting on KeyboardInterrupt" in r.getMessage() for r in caplog.records)
