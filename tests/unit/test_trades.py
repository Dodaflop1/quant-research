"""Tests for trade parsing and the backfill layer.

The parsing tests encode a mistake already made once on this project: the live
Kalshi payload uses unit-suffixed field names that the documentation does not
always show, and a parser written from the documented names returned zero rows
against production while looking healthy.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from quant.common.api.kalshi import (
    HISTORICAL_TRADES_PATH,
    LIVE_TRADES_PATH,
    KalshiAPIError,
    RawResponse,
    parse_trade,
)
from quant.ingest.trade_backfill import (
    Checkpoint,
    TradeBackfill,
    days_between,
    parse_trade_files,
)


def make_record(**overrides) -> dict:
    record = {
        "trade_id": "t-1",
        "ticker": "KXTEST-26AUG23-A",
        "count_fp": "25.00",
        "yes_price_dollars": "0.6300",
        "no_price_dollars": "0.3700",
        "taker_outcome_side": "yes",
        "taker_book_side": "ask",
        "created_time": "2026-08-23T14:30:00Z",
        "is_block_trade": False,
    }
    record.update(overrides)
    return record


# -- parsing ----------------------------------------------------------------


def test_parses_the_unit_suffixed_live_shape():
    trade = parse_trade(make_record())
    assert trade.contract_id == "KXTEST-26AUG23-A"
    assert trade.price == pytest.approx(63.0)
    assert trade.size == pytest.approx(25.0)
    assert trade.taker_side == "buy"
    assert trade.timestamp == datetime(2026, 8, 23, 14, 30, tzinfo=timezone.utc)


def test_falls_back_to_the_bare_documented_names():
    record = make_record()
    del record["count_fp"]
    del record["yes_price_dollars"]
    record["count"] = 25
    record["yes_price"] = 63
    trade = parse_trade(record)
    assert trade.price == pytest.approx(63.0)
    assert trade.size == pytest.approx(25.0)


def test_a_no_side_taker_is_a_yes_seller():
    """Buying NO at q and selling YES at 100-q are the same trade.

    Collapsing both into one YES-terms direction is what makes buy- and
    sell-initiated flow comparable across markets. Getting this backwards would
    invert every cross-excitation term in a bivariate fit while leaving the
    univariate results untouched - a bug that hides until the interesting result.
    """
    assert parse_trade(make_record(taker_outcome_side="yes")).taker_side == "buy"
    assert parse_trade(make_record(taker_outcome_side="no")).taker_side == "sell"
    assert parse_trade(make_record(taker_outcome_side="YES")).taker_side == "buy"


def test_rejects_an_unrecognised_taker_side_rather_than_guessing():
    with pytest.raises(ValueError, match="taker_outcome_side"):
        parse_trade(make_record(taker_outcome_side="both"))
    with pytest.raises(ValueError, match="taker_outcome_side"):
        parse_trade(make_record(taker_outcome_side=None))


def test_accepts_a_unix_timestamp_as_well_as_iso():
    unix = int(datetime(2026, 8, 23, 14, 30, tzinfo=timezone.utc).timestamp())
    trade = parse_trade(make_record(created_time=unix))
    assert trade.timestamp == datetime(2026, 8, 23, 14, 30, tzinfo=timezone.utc)


def test_naive_timestamps_are_treated_as_utc_not_local():
    trade = parse_trade(make_record(created_time="2026-08-23T14:30:00"))
    assert trade.timestamp == datetime(2026, 8, 23, 14, 30, tzinfo=timezone.utc)


def test_missing_price_and_size_are_errors_not_defaults():
    record = make_record()
    del record["yes_price_dollars"]
    with pytest.raises(ValueError, match="YES price"):
        parse_trade(record)

    record = make_record()
    del record["count_fp"]
    with pytest.raises(ValueError, match="size"):
        parse_trade(record)


# -- windows and checkpointing ----------------------------------------------


def test_days_between_is_inclusive_and_newest_first():
    days = list(days_between(date(2026, 8, 20), date(2026, 8, 23)))
    assert days == [
        date(2026, 8, 23),
        date(2026, 8, 22),
        date(2026, 8, 21),
        date(2026, 8, 20),
    ]


def test_a_single_day_range_yields_that_day():
    assert list(days_between(date(2026, 8, 23), date(2026, 8, 23))) == [
        date(2026, 8, 23)
    ]


def test_checkpoint_round_trips_and_resumes(tmp_path):
    path = tmp_path / "cp.json"
    cp = Checkpoint(path)
    assert not cp.done("ABC", date(2026, 8, 23))
    cp.mark("ABC", date(2026, 8, 23))
    assert cp.done("ABC", date(2026, 8, 23))
    assert not cp.done("ABC", date(2026, 8, 22))
    assert not cp.done("XYZ", date(2026, 8, 23))

    assert Checkpoint(path).done("ABC", date(2026, 8, 23))


def test_checkpoint_treats_market_wide_and_per_ticker_windows_as_distinct():
    """A whole-tape fetch must not mark a per-ticker window complete."""
    assert Checkpoint.key(None, date(2026, 8, 23)) != Checkpoint.key(
        "ABC", date(2026, 8, 23)
    )


def test_a_corrupt_checkpoint_costs_a_redownload_not_a_crash(tmp_path):
    path = tmp_path / "cp.json"
    path.write_text("{not json")
    cp = Checkpoint(path)
    assert not cp.done("ABC", date(2026, 8, 23))


# -- the backfill loop ------------------------------------------------------


class _FakeClient:
    """Returns one page per endpoint, recording the windows it was asked for."""

    def __init__(self, pages_by_path=None, fail_paths=()):
        self.calls: list[dict] = []
        self.pages_by_path = pages_by_path or {}
        self.fail_paths = set(fail_paths)

    def iter_all_trades(self, ticker=None, min_ts=None, max_ts=None, limit=1000):
        for path in (LIVE_TRADES_PATH, HISTORICAL_TRADES_PATH):
            self.calls.append(
                {"path": path, "ticker": ticker, "min_ts": min_ts, "max_ts": max_ts}
            )
            if path in self.fail_paths:
                continue
            now = datetime.now(timezone.utc)
            yield RawResponse(
                path=path,
                params={},
                requested_at=now,
                received_at=now,
                payload={"trades": self.pages_by_path.get(path, [])},
            )


def test_backfill_queries_both_endpoints_for_every_window(tmp_path):
    client = _FakeClient()
    backfill = TradeBackfill(client, out_dir=tmp_path)
    backfill.run(date(2026, 8, 23), date(2026, 8, 23))
    paths = {call["path"] for call in client.calls}
    assert paths == {LIVE_TRADES_PATH, HISTORICAL_TRADES_PATH}


def test_backfill_window_covers_exactly_one_utc_day(tmp_path):
    client = _FakeClient()
    backfill = TradeBackfill(client, out_dir=tmp_path)
    backfill.run(date(2026, 8, 23), date(2026, 8, 23))
    call = client.calls[0]
    assert call["max_ts"] - call["min_ts"] == 86_400
    assert datetime.fromtimestamp(call["min_ts"], tz=timezone.utc) == datetime(
        2026, 8, 23, tzinfo=timezone.utc
    )


def test_backfill_skips_days_already_checkpointed(tmp_path):
    client = _FakeClient()
    TradeBackfill(client, out_dir=tmp_path).run(date(2026, 8, 22), date(2026, 8, 23))
    first_round = len(client.calls)

    resumed = TradeBackfill(client, out_dir=tmp_path)
    stats = resumed.run(date(2026, 8, 22), date(2026, 8, 23))
    assert len(client.calls) == first_round, "re-fetched an already-complete window"
    assert stats.days_skipped == 2
    assert stats.days_done == 0


def test_a_failing_day_is_left_unmarked_so_it_retries(tmp_path):
    class _Exploding:
        def __init__(self):
            self.attempts = 0

        def iter_all_trades(self, **kwargs):
            self.attempts += 1
            raise KalshiAPIError(500, "boom", "/markets/trades")
            yield  # pragma: no cover - makes this a generator

    client = _Exploding()
    backfill = TradeBackfill(client, out_dir=tmp_path)
    stats = backfill.run(date(2026, 8, 23), date(2026, 8, 23))
    assert stats.errors == 1
    assert stats.days_done == 0
    assert not backfill.checkpoint.done(None, date(2026, 8, 23))


def test_one_endpoint_failing_does_not_lose_the_other(tmp_path):
    client = _FakeClient(
        pages_by_path={LIVE_TRADES_PATH: [make_record()]},
        fail_paths={HISTORICAL_TRADES_PATH},
    )
    backfill = TradeBackfill(client, out_dir=tmp_path)
    stats = backfill.run(date(2026, 8, 23), date(2026, 8, 23))
    assert stats.trades_seen == 1
    assert stats.days_done == 1


# -- deduplication ----------------------------------------------------------


def _write_pages(tmp_path, pages) -> list:
    path = tmp_path / "kalshi_trades_2026-08-23.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for trades in pages:
            fh.write(
                json.dumps(
                    {
                        "kind": "trades_page",
                        "ticker": None,
                        "day": "2026-08-23",
                        "path": LIVE_TRADES_PATH,
                        "requested_at": "2026-08-23T00:00:00+00:00",
                        "received_at": "2026-08-23T00:00:01+00:00",
                        "payload": {"trades": trades},
                    }
                )
                + "\n"
            )
    return [path]


def test_the_same_trade_from_both_endpoints_is_counted_once(tmp_path):
    """Overlap is by design; double-counting it would inflate the arrival rate.

    Which is precisely the quantity the whole project measures.
    """
    duplicate = make_record(trade_id="dup")
    paths = _write_pages(tmp_path, [[duplicate], [duplicate]])
    assert len(list(parse_trade_files(paths))) == 1


def test_distinct_trades_all_survive(tmp_path):
    paths = _write_pages(
        tmp_path,
        [[make_record(trade_id="a"), make_record(trade_id="b")], [make_record(trade_id="c")]],
    )
    trades = list(parse_trade_files(paths))
    assert len(trades) == 3


def test_one_unparseable_trade_does_not_stop_the_file(tmp_path):
    paths = _write_pages(
        tmp_path,
        [[make_record(trade_id="ok-1"), make_record(trade_id="bad", taker_outcome_side="?"), make_record(trade_id="ok-2")]],
    )
    trades = list(parse_trade_files(paths))
    assert {t.contract_id for t in trades} == {"KXTEST-26AUG23-A"}
    assert len(trades) == 2


def test_non_trade_records_are_ignored(tmp_path):
    path = tmp_path / "kalshi_trades_2026-08-23.jsonl"
    path.write_text(
        json.dumps({"kind": "orderbook", "ticker": "X"})
        + "\n"
        + "{not json\n"
        + json.dumps(
            {"kind": "trades_page", "payload": {"trades": [make_record()]}}
        )
        + "\n"
    )
    assert len(list(parse_trade_files([path]))) == 1
