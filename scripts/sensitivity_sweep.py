#!/usr/bin/env python3
"""How much do the diffusion results depend on two arbitrary preprocessing constants?

`fit_diffusion.py` has two knobs that were set by judgement rather than derived:
`--merge-window` (1 ms) decides when several trade prints are one order arrival,
and `--period` (1 day) sets the seasonal profile. Every branching ratio in the
diffusion write-up is conditional on both. A reviewer will ask how sensitive the
result is, and "we picked 1 ms" is not an answer.

This runs the existing fitter over a 4 x 3 grid and reports how far the numbers
move. Nothing here re-implements the fitting or modifies `fit_diffusion.py`.

The question it is really asking
--------------------------------
Deseasonalising currently moves the median per-market branching ratio by
**+0.004**. That is suspiciously small: on simulated arrivals with zero
self-excitation, seasonality alone fabricates a branching ratio of 0.79-0.95.
A correction that changes almost nothing is either working perfectly or not
engaging at all, and those look identical from one cell.

- **If `n` and the diagnostics are flat across the grid**, preprocessing is not
  what drives the slow arm's bimodality, and attention moves to heterogeneity
  across markets or non-stationarity within windows.
- **If they move a lot**, the current numbers are an artifact of two constants
  and the write-up cannot quote them without this table beside it.

Either outcome is reportable. The table goes in the methodology document.

Usage
-----
    python scripts/sensitivity_sweep.py --data ./data \\
        --out results/diffusion/sensitivity_sweep.json \\
        --report results/diffusion/sensitivity_sweep.md
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

# Second pass. The first grid ran 0.0001 / 0.001 / 0.01 / 0.1 and the three
# smallest were identical to three decimals, because ~30% of prints are *exact*
# ties and any positive tolerance catches all of them. The whole informative
# region is above 10 ms, where naive `n` jumped 0.466 -> 0.723 on 0.4% more
# prints merged. 0.01 and 0.1 are kept as anchors to the first pass (and are
# already cached, so they cost nothing to re-include).
MERGE_WINDOWS = [0.01, 0.03, 0.1, 0.3, 1.0]
PERIODS = [900.0, 3600.0, 86400.0]

# Only the two swept flags and the fixed data/threshold are passed. Everything
# else stays at fit_diffusion's own defaults - --window-events 5000,
# --holdout 0.25 (a FRACTION, not a count), --bins 24. Re-declaring a default
# here would let the two scripts drift apart silently.
PRINTS_RE = re.compile(r"^(\S+)\s+\((\d[\d,]*) prints\)", re.MULTILINE)


ENV_FAILURE = ("ModuleNotFoundError", "ImportError", "No module named")


def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def find_interpreter(explicit: str | None) -> str:
    """The interpreter that can actually import the dependencies.

    `sys.executable` is whichever python launched this script, which on a
    machine with a project virtualenv is usually the wrong one - the sweep
    starts, shells out twelve times, and every cell dies on `import scipy`.
    Prefer the current interpreter when it works, fall back to a project venv,
    and refuse to start otherwise.
    """
    if explicit:
        return explicit
    probe = "import numpy, scipy, quant.diffusion.hawkes.model"
    root = Path(__file__).resolve().parent.parent
    env = {"PYTHONPATH": str(root / "src")}
    candidates = [sys.executable,
                  str(root / ".venv" / "Scripts" / "python.exe"),
                  str(root / ".venv" / "bin" / "python"),
                  str(root / "venv" / "Scripts" / "python.exe"),
                  str(root / "venv" / "bin" / "python")]
    for cand in candidates:
        if cand != sys.executable and not Path(cand).exists():
            continue
        import os
        proc = subprocess.run([cand, "-c", probe], capture_output=True, text=True,
                              env={**os.environ, **env}, check=False)
        if proc.returncode == 0:
            if cand != sys.executable:
                print(f"  using {cand} (the interpreter running this script lacks the deps)")
            return cand
    raise SystemExit(
        "No interpreter found that can import numpy, scipy and the quant package.\n"
        f"Tried: {', '.join(candidates)}\n"
        "Activate the project environment, or pass --python explicitly:\n"
        "  .venv\\Scripts\\python.exe scripts\\sensitivity_sweep.py\n"
        "  python scripts/sensitivity_sweep.py --python .venv/Scripts/python.exe"
    )


def raw_print_counts(py: str, data: Path, min_trades: int, timeout: int) -> dict[str, int]:
    """Prints per market BEFORE any merging, from `--ties` (which does not fit).

    This is the honest denominator for "fraction of events surviving the merge".
    Deriving it from two fitted summaries - as an earlier version did - gives a
    number that is always 1.0, because both sides of that ratio are counts from
    the same already-merged series.
    """
    proc = run(
        [py, "scripts/fit_diffusion.py", "--data", str(data),
         "--min-trades", str(min_trades), "--ties"],
        timeout,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-600:]
        raise SystemExit(
            f"`fit_diffusion.py --ties` failed (exit {proc.returncode}) before the sweep "
            f"started.\nThis is not a per-cell problem - fix it once and rerun:\n\n{tail}"
        )
    return {m.group(1): int(m.group(2).replace(",", "")) for m in PRINTS_RE.finditer(proc.stdout)}


def aggregate(payload: dict, raw_counts: dict[str, int]) -> dict[str, Any]:
    """Reduce one fit_diffusion output to the numbers the grid compares.

    Schema, verified against `scripts/fit_diffusion.py` rather than assumed:

        {"config": {...}, "median_per_market_shrink": float,
         "markets": {ticker: {"n_events": int,
                              "naive":          {"summary": {...}, "windows": [...]},
                              "deseasonalised": {"summary": {...}, "windows": [...]},
                              "half_life_seconds": {...},
                              "holdout_ll_gain_per_event": {...},
                              "windows_passing_diagnostics": int,
                              "windows_total": int}}}

    `summary` carries n / median / mean / q25 / q75 - there is no "count" key.
    """
    markets = payload.get("markets") or {}
    naive, deseas, gains, half_lives = [], [], [], []
    passing = total = beat = 0
    merged_events = raw_events = 0

    for ticker, m in markets.items():
        for bucket, sink in (("naive", naive), ("deseasonalised", deseas)):
            median = (m.get(bucket) or {}).get("summary", {}).get("median")
            if median is not None and np.isfinite(median):
                sink.append(float(median))

        gain = (m.get("holdout_ll_gain_per_event") or {}).get("median")
        if gain is not None and np.isfinite(gain):
            gains.append(float(gain))
            beat += gain > 0

        hl = (m.get("half_life_seconds") or {}).get("median")
        if hl is not None and np.isfinite(hl):
            half_lives.append(float(hl))

        passing += int(m.get("windows_passing_diagnostics") or 0)
        total += int(m.get("windows_total") or 0)
        merged_events += int(m.get("n_events") or 0)
        raw_events += raw_counts.get(ticker, 0)

    return {
        "markets": len(markets),
        "n_naive": spread(naive),
        "n_deseasonalised": spread(deseas),
        "median_per_market_shrink": payload.get("median_per_market_shrink"),
        "half_life_seconds": spread(half_lives),
        "holdout_gain_per_event": spread(gains),
        "windows_passing": passing,
        "windows_total": total,
        "markets_beating_poisson": beat,
        "events_after_merge": merged_events,
        "events_raw": raw_events or None,
        "fraction_surviving_merge": merged_events / raw_events if raw_events else None,
    }


def spread(values: list[float]) -> dict[str, Any]:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return {"median": None, "p10": None, "p90": None, "count": 0}
    return {
        "median": float(np.median(arr)),
        "p10": float(np.quantile(arr, 0.10)),
        "p90": float(np.quantile(arr, 0.90)),
        "count": int(arr.size),
    }


def cell_key(merge: float, period: float) -> str:
    return f"merge{merge:g}_period{period:g}"


def table(cells: dict[str, dict], title: str, render) -> list[str]:
    head = "| merge \\ period | " + " | ".join(f"{p:g}s" for p in PERIODS) + " |"
    rule = "|---" * (len(PERIODS) + 1) + "|"
    lines = [f"### {title}", "", head, rule]
    for merge in MERGE_WINDOWS:
        row = [f"**{merge:g}s**"]
        for period in PERIODS:
            cell = cells.get(cell_key(merge, period))
            if not cell:
                row.append("–")
            elif not cell.get("ok"):
                row.append("**ERR**")
            else:
                row.append(render(cell["summary"]))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return lines


def fmt_spread(block: dict) -> str:
    if not block or block.get("median") is None:
        return "–"
    return f"{block['median']:.3f} <sub>[{block['p10']:.3f}, {block['p90']:.3f}]</sub>"


def build_report(cells: dict[str, dict], meta: dict) -> str:
    ok = [c for c in cells.values() if c.get("ok")]
    lines = [
        "# Sensitivity of the diffusion fits to preprocessing",
        "",
        f"{len(ok)} of {len(cells)} cells completed. Rows are `--merge-window`, "
        "columns are `--period`. Every other flag is at `fit_diffusion.py`'s default.",
        "",
        "Branching-ratio cells show the median across markets with the "
        "10th-90th percentile band beneath.",
        "",
    ]
    lines += table(cells, "Branching ratio — naive", lambda s: fmt_spread(s["n_naive"]))
    lines += table(cells, "Branching ratio — deseasonalised",
                   lambda s: fmt_spread(s["n_deseasonalised"]))
    lines += table(cells, "Median per-market shrink (naive − deseasonalised)",
                   lambda s: "–" if s.get("median_per_market_shrink") is None
                   else f"{s['median_per_market_shrink']:+.4f}")
    lines += table(cells, "Windows passing both diagnostics",
                   lambda s: f"{s['windows_passing']}/{s['windows_total']}")
    lines += table(cells, "Markets beating held-out Poisson",
                   lambda s: f"{s['markets_beating_poisson']}/{s['markets']}")
    lines += table(cells, "Events surviving the merge",
                   lambda s: "–" if s.get("fraction_surviving_merge") is None
                   else f"{s['fraction_surviving_merge']:.1%}")

    if ok:
        med = [c["summary"]["n_deseasonalised"]["median"] for c in ok
               if c["summary"]["n_deseasonalised"]["median"] is not None]
        rates = [c["summary"]["windows_passing"] / c["summary"]["windows_total"]
                 for c in ok if c["summary"]["windows_total"]]
        lines += ["## Verdict", ""]
        if med:
            lines.append(
                f"Deseasonalised branching ratio ranges **{min(med):.3f} to {max(med):.3f}** "
                f"across the grid — a spread of **{max(med) - min(med):.3f}**. "
                f"The real slow arm's bimodal split is 0.23 vs 0.88, a spread of 0.65."
            )
            lines.append("")
            lines.append(
                "A grid spread well below 0.65 means these two constants are not what "
                "produces the split, and the explanation lies elsewhere — heterogeneity "
                "across markets, or non-stationarity within windows. A grid spread "
                "approaching it means the published numbers are an artifact of the "
                "constants and must be quoted with this table."
                if max(med) - min(med) < 0.65 else
                "A spread this large means the published branching ratios are "
                "conditional on preprocessing choices to a degree that undermines them."
            )
        if rates:
            lines += ["", f"Diagnostic pass rate ranges {min(rates):.0%} to {max(rates):.0%}."]
    if meta.get("errors"):
        lines += ["", "## Failed cells", ""]
        for k, e in meta["errors"].items():
            lines.append(f"- `{k}`: {e}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", type=Path, default=Path("./data"))
    ap.add_argument("--min-trades", type=int, default=2000)
    ap.add_argument("--out", type=Path, default=Path("results/diffusion/sensitivity_sweep.json"))
    ap.add_argument("--report", type=Path, default=Path("results/diffusion/sensitivity_sweep.md"))
    ap.add_argument("--cache", type=Path, default=Path("results/diffusion/.sweep_cache"))
    ap.add_argument("--timeout", type=int, default=5400, help="seconds per cell")
    ap.add_argument("--refresh", action="store_true", help="ignore cached successes too")
    ap.add_argument("--python", default=None,
                    help="interpreter for the fits; default auto-detects a working one")
    args = ap.parse_args(argv)

    args.cache.mkdir(parents=True, exist_ok=True)
    py = find_interpreter(args.python)

    print("collecting raw print counts (--ties, no fitting)")
    raw_counts = raw_print_counts(py, args.data, args.min_trades, args.timeout)
    print(f"  {len(raw_counts)} markets, {sum(raw_counts.values()):,} raw prints\n")

    cells: dict[str, dict] = {}
    errors: dict[str, str] = {}

    for merge in MERGE_WINDOWS:
        for period in PERIODS:
            key = cell_key(merge, period)
            cached = args.cache / f"{key}.json"
            if cached.exists() and not args.refresh:
                prior = json.loads(cached.read_text(encoding="utf-8"))
                if prior.get("ok"):
                    cells[key] = prior
                    print(f"{key}: cached")
                    continue
                print(f"{key}: cached failure, retrying")

            print(f"{key}: running", flush=True)
            out = args.cache / f"{key}.fit.json"
            try:
                proc = run(
                    [py, "scripts/fit_diffusion.py",
                     "--data", str(args.data),
                     "--min-trades", str(args.min_trades),
                     "--merge-window", str(merge),
                     "--period", str(period),
                     "--out", str(out)],
                    args.timeout,
                )
                if proc.returncode != 0:
                    detail = (proc.stderr or proc.stdout or "").strip()
                    if any(marker in detail for marker in ENV_FAILURE):
                        raise SystemExit(
                            "The fitter cannot import its dependencies. Every cell would "
                            "fail the same way, so the sweep is stopping rather than "
                            "producing twelve copies of one error:\n\n"
                            + detail[-600:]
                            + "\n\nRerun once the environment is fixed; completed cells "
                              "are cached and will not refit."
                        )
                    raise RuntimeError(f"exit {proc.returncode}: {detail[-500:]}")
                if not out.exists():
                    raise RuntimeError("fit_diffusion produced no output file")
                record = {"ok": True, "merge_window": merge, "period": period,
                          "summary": aggregate(json.loads(out.read_text(encoding="utf-8")),
                                               raw_counts)}
                s = record["summary"]
                print(f"  n_deseas {s['n_deseasonalised']['median']}, "
                      f"diagnostics {s['windows_passing']}/{s['windows_total']}")
            except Exception as exc:  # one bad cell must not lose the other eleven
                record = {"ok": False, "merge_window": merge, "period": period,
                          "error": f"{type(exc).__name__}: {exc}"}
                errors[key] = record["error"]
                print(f"  FAILED: {record['error']}")

            cached.write_text(json.dumps(record, indent=2, default=float), encoding="utf-8")
            cells[key] = record

    meta = {"merge_windows": MERGE_WINDOWS, "periods": PERIODS,
            "raw_print_counts": raw_counts, "errors": errors}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"meta": meta, "cells": cells}, indent=2, default=float),
                        encoding="utf-8")
    report = build_report(cells, meta)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")

    print(f"\nwrote {args.out}\nwrote {args.report}\n")
    print(report)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
