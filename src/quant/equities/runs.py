"""Local, immutable saved experiments and portable research bundles."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
import zipfile

import pandas as pd

from .data import load_frame
from .engine import BacktestResult
from .schema import Hypothesis

ENGINE_VERSION = "equities-ledger-v1"


@dataclass(frozen=True)
class SavedRun:
    run_id: str
    created_at: str
    hypothesis: Hypothesis
    initial_investment: float
    source: str
    split_date: date | None
    data_hash: str
    parent_run_id: str | None
    data: pd.DataFrame
    result: BacktestResult


@dataclass(frozen=True)
class RunComparison:
    """The changed assumptions and outcome summary for two recorded runs."""

    assumptions: pd.DataFrame
    outcomes: pd.DataFrame


class RunStore:
    """SQLite index plus run-specific CSV snapshots kept under a local data directory."""

    def __init__(self, root: str | Path = "data/equities_runs") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "runs.sqlite3"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    hypothesis_json TEXT NOT NULL,
                    initial_investment REAL NOT NULL,
                    source TEXT NOT NULL,
                    split_date TEXT,
                    data_hash TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    parent_run_id TEXT REFERENCES runs(run_id)
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
            if "parent_run_id" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN parent_run_id TEXT")

    @staticmethod
    def _csv_bytes(frame: pd.DataFrame, *, include_index: bool = False) -> bytes:
        return frame.to_csv(index=include_index, date_format="%Y-%m-%dT%H:%M:%S").encode("utf-8")

    def save(
        self,
        hypothesis: Hypothesis,
        initial_investment: float,
        source: str,
        split_date: date | None,
        data: pd.DataFrame,
        result: BacktestResult,
        parent_run_id: str | None = None,
    ) -> SavedRun:
        """Write a new immutable snapshot; existing saved runs are never overwritten."""
        run_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        if parent_run_id:
            with self._connect() as connection:
                if connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (parent_run_id,)).fetchone() is None:
                    raise ValueError("Parent saved experiment was not found")
        run_dir = self.root / run_id
        run_dir.mkdir()
        normalized_data = load_frame(data)
        data_bytes = self._csv_bytes(normalized_data)
        data_hash = sha256(data_bytes).hexdigest()
        (run_dir / "prices.csv").write_bytes(data_bytes)
        result.daily.reset_index().to_csv(run_dir / "daily_ledger.csv", index=False, date_format="%Y-%m-%dT%H:%M:%S")
        result.trades.to_csv(run_dir / "trades.csv", index=False, date_format="%Y-%m-%dT%H:%M:%S")
        skipped = result.skipped_signals
        if skipped.empty and not list(skipped.columns):
            skipped = pd.DataFrame(columns=["signal_date", "ticker", "reason"])
        skipped.to_csv(run_dir / "skipped_signals.csv", index=False, date_format="%Y-%m-%dT%H:%M:%S")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, created_at, hypothesis_json, initial_investment, source,
                    split_date, data_hash, engine_version, parent_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    created_at,
                    hypothesis.model_dump_json(),
                    initial_investment,
                    source,
                    split_date.isoformat() if split_date else None,
                    data_hash,
                    ENGINE_VERSION,
                    parent_run_id,
                ),
            )
        return self.load(run_id)

    def list_runs(self, limit: int = 25) -> pd.DataFrame:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, created_at, source, initial_investment, data_hash,
                       engine_version, parent_run_id
                FROM runs ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return pd.DataFrame(rows, columns=[
            "run_id", "created_at", "source", "initial_investment", "data_hash",
            "engine_version", "parent_run_id",
        ])

    def load(self, run_id: str) -> SavedRun:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Saved experiment was not found")
        run_dir = self.root / run_id
        data = load_frame(pd.read_csv(run_dir / "prices.csv"))
        daily = pd.read_csv(run_dir / "daily_ledger.csv", parse_dates=["date"]).set_index("date")
        trades = pd.read_csv(run_dir / "trades.csv", parse_dates=["signal_date", "entry_date", "exit_date"])
        skipped_path = run_dir / "skipped_signals.csv"
        skipped = pd.read_csv(skipped_path, parse_dates=["signal_date"])
        return SavedRun(
            run_id=row["run_id"],
            created_at=row["created_at"],
            hypothesis=Hypothesis.model_validate_json(row["hypothesis_json"]),
            initial_investment=float(row["initial_investment"]),
            source=row["source"],
            split_date=date.fromisoformat(row["split_date"]) if row["split_date"] else None,
            data_hash=row["data_hash"],
            parent_run_id=row["parent_run_id"],
            data=data,
            result=BacktestResult(daily=daily, trades=trades, skipped_signals=skipped),
        )

    @staticmethod
    def _assumption_values(saved: SavedRun) -> dict[str, object]:
        values = saved.hypothesis.model_dump(mode="json")
        return {
            **values,
            "initial_investment": saved.initial_investment,
            "source": saved.source,
            "split_date": saved.split_date.isoformat() if saved.split_date else None,
            "data_sha256": saved.data_hash,
            "parent_run_id": saved.parent_run_id,
        }

    @staticmethod
    def _display(value: object) -> str:
        if value is None:
            return "—"
        if isinstance(value, float):
            return f"{value:g}"
        if isinstance(value, (list, dict)):
            return json.dumps(value, sort_keys=True)
        return str(value)

    def compare(self, first_run_id: str, second_run_id: str) -> RunComparison:
        """Compare recorded snapshots; neither historical result is recalculated."""
        if first_run_id == second_run_id:
            raise ValueError("Choose two different saved experiments to compare")
        first, second = self.load(first_run_id), self.load(second_run_id)
        first_values, second_values = self._assumption_values(first), self._assumption_values(second)
        changes = [
            {"assumption": field, "first run": self._display(first_values.get(field)), "second run": self._display(second_values.get(field))}
            for field in sorted(set(first_values) | set(second_values))
            if first_values.get(field) != second_values.get(field)
        ]

        def outcome(saved: SavedRun) -> dict[str, float | int]:
            daily = saved.result.daily
            ending_value = float(daily["portfolio_value"].iloc[-1]) if not daily.empty else saved.initial_investment
            peak = daily["portfolio_value"].cummax().clip(lower=saved.initial_investment) if not daily.empty else pd.Series(dtype=float)
            drawdown = float((daily["portfolio_value"] / peak - 1).min()) if not daily.empty else 0.0
            return {
                "Net portfolio return": ending_value / saved.initial_investment - 1,
                "Ending portfolio value": ending_value,
                "Maximum drawdown": drawdown,
                "Completed trades": len(saved.result.trades),
                "Excluded signals": len(saved.result.skipped_signals),
            }

        first_outcome, second_outcome = outcome(first), outcome(second)
        outcomes = pd.DataFrame([
            {"outcome": metric, "first run": first_outcome[metric], "second run": second_outcome[metric]}
            for metric in first_outcome
        ])
        return RunComparison(assumptions=pd.DataFrame(changes, columns=["assumption", "first run", "second run"]), outcomes=outcomes)

    def export_bundle(self, run_id: str) -> bytes:
        """Return a self-contained zip with data, outputs and human-readable metadata."""
        saved = self.load(run_id)
        metadata = {
            "run_id": saved.run_id,
            "created_at": saved.created_at,
            "source": saved.source,
            "initial_investment": saved.initial_investment,
            "split_date": saved.split_date.isoformat() if saved.split_date else None,
            "data_sha256": saved.data_hash,
            "engine_version": ENGINE_VERSION,
            "parent_run_id": saved.parent_run_id,
            "hypothesis": saved.hypothesis.model_dump(mode="json"),
        }
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("metadata.json", json.dumps(metadata, indent=2, sort_keys=True))
            archive.writestr("prices.csv", self._csv_bytes(saved.data))
            archive.writestr("daily_ledger.csv", self._csv_bytes(saved.result.daily.reset_index()))
            archive.writestr("trades.csv", self._csv_bytes(saved.result.trades))
            archive.writestr("skipped_signals.csv", self._csv_bytes(saved.result.skipped_signals))
        return bundle.getvalue()
