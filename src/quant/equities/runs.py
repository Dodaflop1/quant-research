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
    data: pd.DataFrame
    result: BacktestResult


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
                    engine_version TEXT NOT NULL
                )
                """
            )

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
    ) -> SavedRun:
        """Write a new immutable snapshot; existing saved runs are never overwritten."""
        run_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
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
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    created_at,
                    hypothesis.model_dump_json(),
                    initial_investment,
                    source,
                    split_date.isoformat() if split_date else None,
                    data_hash,
                    ENGINE_VERSION,
                ),
            )
        return self.load(run_id)

    def list_runs(self, limit: int = 25) -> pd.DataFrame:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id, created_at, source, initial_investment, data_hash, engine_version FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return pd.DataFrame(rows, columns=["run_id", "created_at", "source", "initial_investment", "data_hash", "engine_version"])

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
            data=data,
            result=BacktestResult(daily=daily, trades=trades, skipped_signals=skipped),
        )

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
