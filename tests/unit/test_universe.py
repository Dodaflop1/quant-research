"""Tests for universe pinning.

A coverage audit of the first 39 hours of live collection found 298 distinct
tickers across three collector restarts and **zero** that spanned the whole
window: discovery re-selects by live volume on every start, so each restart
swaps out part of the panel. These tests cover the fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from collect_kalshi import load_pinned_tickers, save_universe  # noqa: E402


def test_reads_a_plain_ticker_list(tmp_path):
    path = tmp_path / "u.txt"
    path.write_text("AAA\nBBB\nCCC\n")
    assert load_pinned_tickers(path) == {"AAA", "BBB", "CCC"}


def test_ignores_comments_and_blank_lines(tmp_path):
    """The file the collector writes has a comment header; it must round-trip."""
    path = tmp_path / "u.txt"
    path.write_text(
        "# Kalshi collection universe, resolved 2026-08-24T20:00:00+00:00\n"
        "# 2 tickers.\n"
        "\n"
        "AAA\n"
        "   \n"
        "  BBB  \n"
    )
    assert load_pinned_tickers(path) == {"AAA", "BBB"}


def test_a_missing_file_fails_loudly(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        load_pinned_tickers(tmp_path / "nope.txt")


def test_an_empty_file_fails_rather_than_collecting_nothing(tmp_path):
    """Silently collecting zero markets would look like a healthy idle run."""
    path = tmp_path / "u.txt"
    path.write_text("# only comments\n\n")
    with pytest.raises(SystemExit, match="no tickers"):
        load_pinned_tickers(path)


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "nested" / "universe.txt"
    tickers = ["BBB", "AAA", "CCC"]
    save_universe(path, tickers)
    assert load_pinned_tickers(path) == set(tickers)


def test_saved_universe_is_sorted_so_diffs_are_readable(tmp_path):
    path = tmp_path / "universe.txt"
    save_universe(path, ["CCC", "AAA", "BBB"])
    body = [
        line
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert body == ["AAA", "BBB", "CCC"]


def test_saved_universe_records_the_count_and_a_timestamp(tmp_path):
    path = tmp_path / "universe.txt"
    save_universe(path, ["AAA", "BBB"])
    header = path.read_text().splitlines()[0]
    assert "resolved" in header
    assert "2 tickers" in path.read_text()
