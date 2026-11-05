"""Immutable run artifacts and an atomic, resumable execution journal.

The SQLite journal commits step state and JSON results in one transaction. Large
artifacts use atomic, create-only files within the run directory. Each run is
bound to configuration and dataset hashes; this module performs no provider work.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


RUN_SCHEMA_VERSION = "1.0"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{0,99}$")
_STEP_KIND = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_hash(value: str, label: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _valid_step_part(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"{label} must be a non-empty identifier up to 200 characters")
    return value


def _json_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be finite JSON data") from exc


def _atomic_create(path: Path, data: bytes) -> None:
    """Publish a fully written file only if its final name does not exist."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link atomically publishes without replacing an existing file.
        # The temporary and destination paths are on the same filesystem.
        os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _safe_relative_path(name: str) -> PurePosixPath:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("artifact name must be a relative POSIX path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError("artifact name must stay inside the run directory")
    if path.parts[0] in ("manifest.json", "journal.sqlite3"):
        raise ValueError("artifact name uses a reserved run path")
    return path


@dataclass(frozen=True)
class StepKey:
    kind: str
    item_id: str
    metric_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not _STEP_KIND.fullmatch(self.kind):
            raise ValueError("kind must be a lowercase step type")
        _valid_step_part(self.item_id, "item_id")
        if self.metric_id:
            _valid_step_part(self.metric_id, "metric_id")
        elif self.kind == "metric":
            raise ValueError("metric steps require a metric_id")


@dataclass(frozen=True)
class StepRecord:
    key: StepKey
    input_hash: str
    status: str
    attempts: int
    updated_at: str
    result: Any | None
    error_code: str | None


@dataclass(frozen=True)
class StepAttempt:
    key: StepKey
    attempt: int
    status: str
    started_at: str
    finished_at: str | None
    error_code: str | None


class RunStore:
    """One immutable run directory with a transactional per-step journal."""

    def __init__(self, path: Path, manifest: Mapping[str, Any]):
        self.path = path
        self.manifest = dict(manifest)
        self.db_path = path / "journal.sqlite3"

    @classmethod
    def create(cls, root: str | Path, *, configuration_hash: str, dataset_hash: str) -> RunStore:
        _valid_hash(configuration_hash, "configuration_hash")
        _valid_hash(dataset_hash, "dataset_hash")
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        for _ in range(10):
            run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:16]}"
            path = root / run_id
            try:
                path.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError("could not reserve a unique run ID")
        manifest = {
            "version": RUN_SCHEMA_VERSION,
            "run_id": run_id,
            "created_at": _utc_now(),
            "configuration_hash": configuration_hash,
            "dataset_hash": dataset_hash,
        }
        _atomic_create(path / "manifest.json", _json_bytes(manifest))
        store = cls(path, manifest)
        store._init_db()
        return store

    @classmethod
    def open(
        cls, root: str | Path, run_id: str, *, configuration_hash: str, dataset_hash: str
    ) -> RunStore:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run_id")
        _valid_hash(configuration_hash, "configuration_hash")
        _valid_hash(dataset_hash, "dataset_hash")
        path = Path(root) / run_id
        with (path / "manifest.json").open("r", encoding="utf-8") as stream:
            manifest = json.load(stream)
        if not isinstance(manifest, dict) or manifest.get("version") != RUN_SCHEMA_VERSION or manifest.get("run_id") != run_id:
            raise ValueError("unsupported or inconsistent run manifest")
        if manifest.get("configuration_hash") != configuration_hash:
            raise ValueError("configuration_hash does not match this run")
        if manifest.get("dataset_hash") != dataset_hash:
            raise ValueError("dataset_hash does not match this run")
        if not (path / "journal.sqlite3").is_file():
            raise ValueError("run journal is missing")
        return cls(path, manifest)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            connection.execute("PRAGMA busy_timeout=30000")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """CREATE TABLE steps (
                    kind TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    metric_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','failed','completed')),
                    attempts INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    PRIMARY KEY (kind,item_id,metric_id)
                )"""
            )
            connection.execute(
                """CREATE TABLE step_attempts (
                    kind TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    metric_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','failed','interrupted','completed')),
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_code TEXT,
                    PRIMARY KEY (kind,item_id,metric_id,attempt)
                )"""
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row, key: StepKey) -> StepRecord:
        return StepRecord(
            key=key,
            input_hash=row["input_hash"],
            status=row["status"],
            attempts=row["attempts"],
            updated_at=row["updated_at"],
            result=json.loads(row["result_json"]) if row["result_json"] is not None else None,
            error_code=row["error_code"],
        )

    def get_step(self, key: StepKey) -> StepRecord | None:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM steps WHERE kind=? AND item_id=? AND metric_id=?",
                (key.kind, key.item_id, key.metric_id),
            ).fetchone()
            return self._row_to_record(row, key) if row else None

    def list_attempts(self, key: StepKey) -> tuple[StepAttempt, ...]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """SELECT * FROM step_attempts WHERE kind=? AND item_id=? AND metric_id=?
                ORDER BY attempt""",
                (key.kind, key.item_id, key.metric_id),
            ).fetchall()
            return tuple(
                StepAttempt(
                    key=key,
                    attempt=row["attempt"],
                    status=row["status"],
                    started_at=row["started_at"],
                    finished_at=row["finished_at"],
                    error_code=row["error_code"],
                )
                for row in rows
            )

    def begin_step(
        self,
        key: StepKey,
        *,
        input_hash: str,
        retry_failed: bool = False,
        recover_interrupted: bool = False,
    ) -> StepRecord:
        """Start work or return an already completed matching step.

        Retrying a recorded failure and recovering a stranded ``running`` step
        require separate explicit flags. Neither can overwrite a completed step.
        """

        _valid_hash(input_hash, "input_hash")
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM steps WHERE kind=? AND item_id=? AND metric_id=?",
                (key.kind, key.item_id, key.metric_id),
            ).fetchone()
            if row is not None:
                if row["input_hash"] != input_hash:
                    raise ValueError("input_hash does not match an existing step")
                if row["status"] == "completed":
                    return self._row_to_record(row, key)
                if row["status"] == "failed" and not retry_failed:
                    raise ValueError("failed step requires retry_failed=True")
                if row["status"] == "running" and not recover_interrupted:
                    raise ValueError("running step requires recover_interrupted=True")
                attempts = row["attempts"] + 1
                now = _utc_now()
                if row["status"] == "running":
                    connection.execute(
                        """UPDATE step_attempts SET status='interrupted', finished_at=?
                        WHERE kind=? AND item_id=? AND metric_id=? AND attempt=?""",
                        (now, key.kind, key.item_id, key.metric_id, row["attempts"]),
                    )
                connection.execute(
                    """UPDATE steps SET status='running', attempts=?, updated_at=?,
                    result_json=NULL, error_code=NULL WHERE kind=? AND item_id=? AND metric_id=?""",
                    (attempts, now, key.kind, key.item_id, key.metric_id),
                )
            else:
                now = _utc_now()
                attempts = 1
                connection.execute(
                    """INSERT INTO steps
                    (kind,item_id,metric_id,input_hash,status,attempts,updated_at)
                    VALUES (?,?,?,?,'running',1,?)""",
                    (key.kind, key.item_id, key.metric_id, input_hash, now),
                )
            connection.execute(
                """INSERT INTO step_attempts
                (kind,item_id,metric_id,attempt,status,started_at)
                VALUES (?,?,?,?,'running',?)""",
                (key.kind, key.item_id, key.metric_id, attempts, now),
            )
            row = connection.execute(
                "SELECT * FROM steps WHERE kind=? AND item_id=? AND metric_id=?",
                (key.kind, key.item_id, key.metric_id),
            ).fetchone()
            return self._row_to_record(row, key)

    def complete_step(self, key: StepKey, *, input_hash: str, attempt: int, result: Any) -> StepRecord:
        """Commit the complete JSON result and status in a single transaction."""

        _valid_hash(input_hash, "input_hash")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt <= 0:
            raise ValueError("attempt must be a positive integer")
        result_json = _json_bytes(result).decode("utf-8").strip()
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM steps WHERE kind=? AND item_id=? AND metric_id=?",
                (key.kind, key.item_id, key.metric_id),
            ).fetchone()
            if row is None or row["status"] != "running" or row["input_hash"] != input_hash or row["attempts"] != attempt:
                raise ValueError("only a matching running step can be completed")
            connection.execute(
                """UPDATE steps SET status='completed', updated_at=?, result_json=?, error_code=NULL
                WHERE kind=? AND item_id=? AND metric_id=?""",
                (_utc_now(), result_json, key.kind, key.item_id, key.metric_id),
            )
            connection.execute(
                """UPDATE step_attempts SET status='completed', finished_at=?
                WHERE kind=? AND item_id=? AND metric_id=? AND attempt=?""",
                (_utc_now(), key.kind, key.item_id, key.metric_id, attempt),
            )
        return self.get_step(key)

    def fail_step(self, key: StepKey, *, input_hash: str, attempt: int, error_code: str) -> StepRecord:
        _valid_hash(input_hash, "input_hash")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt <= 0:
            raise ValueError("attempt must be a positive integer")
        if not isinstance(error_code, str) or not _ERROR_CODE.fullmatch(error_code):
            raise ValueError("error_code must be a short lowercase code without details or secrets")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status,input_hash,attempts FROM steps WHERE kind=? AND item_id=? AND metric_id=?",
                (key.kind, key.item_id, key.metric_id),
            ).fetchone()
            if row is None or row[0] != "running" or row[1] != input_hash or row[2] != attempt:
                raise ValueError("only a matching running step can be failed")
            connection.execute(
                """UPDATE steps SET status='failed', updated_at=?, result_json=NULL, error_code=?
                WHERE kind=? AND item_id=? AND metric_id=?""",
                (_utc_now(), error_code, key.kind, key.item_id, key.metric_id),
            )
            connection.execute(
                """UPDATE step_attempts SET status='failed', finished_at=?, error_code=?
                WHERE kind=? AND item_id=? AND metric_id=? AND attempt=?""",
                (_utc_now(), error_code, key.kind, key.item_id, key.metric_id, attempt),
            )
        return self.get_step(key)

    def write_artifact(self, name: str, content: bytes | str) -> Path:
        """Atomically publish a create-only artifact below ``artifacts/``."""

        relative = _safe_relative_path(name)
        if isinstance(content, str):
            data = content.encode("utf-8")
        elif isinstance(content, bytes):
            data = content
        else:
            raise ValueError("artifact content must be bytes or text")
        path = self.path / "artifacts" / Path(*relative.parts)
        _atomic_create(path, data)
        return path

    def write_json_artifact(self, name: str, value: Any) -> Path:
        return self.write_artifact(name, _json_bytes(value))
