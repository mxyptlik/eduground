"""Build a local, app-schema corpus from ``Courses/`` directories.

The tool turns the local course tree into (a) a SQLite database that carries the
API's own SQLAlchemy schema, so evaluation tools importing
``app.db.session.SessionLocal`` (``tools/evaluation/export_course_evidence.py``,
``tools/evaluation/freeze_reviewed_dataset.py``,
``evaluation/program/live.py``) read from it instead of Postgres, and (b)
optional Qdrant points for the worker/API retrieval path.

Each file is parsed, optionally OCR-transcribed and enriched with figure cards,
then normalized and chunked in a child process through the same worker services
used by ``run_ingestion_pipeline``. The parent
persists Source, SourceVersion, Chunk and IngestionJob rows the way
``app.services.database`` writes them, and, when requested, embeds chunk texts
through ONE shared budget-ledger guard held by a persistent worker child
(``load_ledger_guard`` + ``app.services.embeddings.embed_texts``) and upserts
points with the worker's payload/point builders and the uuid5 point-id scheme.

No HTTP API, Clerk, or Postgres is needed. ``--index`` needs a reachable Qdrant.
``--ocr``, ``--vision`` and ``--embed`` are opt-in provider-backed modes, all
using the same $10 pilot ledger and dated price registry.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from decimal import Decimal
import sqlite3
import json
import logging
import mimetypes
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.program.corpus_inventory import inventory_courses  # noqa: E402

SPEND_CAP_USD = "10"
FILE_TIMEOUT_SECONDS = 600
DEFAULT_COLLECTION = "eduground_chunks"
MANIFEST_FILENAME = "manifest.json"
SOURCE_TYPE_BY_SUFFIX = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".txt": "txt", ".md": "md"}

logger = logging.getLogger("build_local_corpus")


def _prefer_worker_path() -> None:
    """Make the worker's ``app`` package win inside child processes.

    Development shells may already expose apps/api, apps/evaluator and
    apps/worker on sys.path; without this the API copy of ``app`` shadows the
    worker one and ``app.services.chunking`` etc. cannot be imported.
    """
    sys.path.insert(0, str(ROOT / "apps" / "worker"))
    for name in ("api", "evaluator"):
        conflicting = str(ROOT / "apps" / name)
        while conflicting in sys.path:
            sys.path.remove(conflicting)


def _prefer_api_path() -> None:
    """Make the API's ``app`` package win in the parent process."""
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    for name in ("worker", "evaluator"):
        conflicting = str(ROOT / "apps" / name)
        while conflicting in sys.path:
            sys.path.remove(conflicting)


def elapsed_ms(start_ns: int) -> float:
    return round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested; mirror the worker's row/point/id conventions).
# ---------------------------------------------------------------------------


def build_qdrant_point_id(source_version_id: str, chunk_index: int) -> str:
    """Deterministic Qdrant point id, mirroring app.services.indexing/database."""
    return str(uuid5(NAMESPACE_URL, f"{source_version_id}:{chunk_index}"))


def strip_nul_bytes(value: str | None) -> str | None:
    """Mirror app.services.database._strip_nul_bytes for Postgres-invalid NULs."""
    if value is None:
        return None
    return str(value).replace("\x00", "")


def sanitize_json_value(value):
    """Mirror app.services.database._sanitize_json_value recursively."""
    if isinstance(value, str):
        return strip_nul_bytes(value)
    if isinstance(value, list):
        return [sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_json_value(item) for key, item in value.items()}
    return value


def build_chunk_rows(
    *,
    source_id: str,
    source_version_id: str,
    module_id: str | None,
    source_type: str,
    chunks: list[dict],
) -> list[dict]:
    """Build chunk rows with the exact fields app.services.database persists."""
    is_presentation = source_type == "pptx"
    now = datetime.now(UTC)
    rows: list[dict] = []
    for chunk in chunks:
        chunk_index = int(chunk["chunk_index"])
        text = strip_nul_bytes(str(chunk["text"])) or ""
        metadata = sanitize_json_value(chunk.get("metadata") or {})
        start_segment = chunk.get("start_segment")
        end_segment = chunk.get("end_segment")
        rows.append(
            {
                "id": str(uuid4()),
                "source_id": source_id,
                "source_version_id": source_version_id,
                "module_id": module_id,
                "chunk_index": chunk_index,
                "token_count": int(chunk["token_count"]),
                "char_count": len(text),
                "text": text,
                "normalized_text": strip_nul_bytes(str(chunk["normalized_text"])) or "",
                "start_page": None if is_presentation else start_segment,
                "end_page": None if is_presentation else end_segment,
                "start_slide": start_segment if is_presentation else None,
                "end_slide": end_segment if is_presentation else None,
                "heading_path": metadata.get("heading_path"),
                "tags": metadata,
                "qdrant_point_id": build_qdrant_point_id(source_version_id, chunk_index),
                "created_at": now,
            }
        )
    return rows


def runtime_payload_fields(chunk: dict) -> dict:
    """Job-payload fields written around app.services.indexing.build_qdrant_payload.

    ``apps/worker/app/jobs/ingestion.py`` stores the DB chunk UUID as
    ``chunk_id`` (the API resolves it via ``session.get(Chunk, chunk_id)`` in
    ``answer_question.py`` and ``evaluation/program/live.py``), flags the point
    as ``indexed`` (``build_retrieval_filters`` filters on that status) and adds
    the page/slide bounds.
    """
    return {
        "chunk_id": chunk["db_chunk_id"],
        "status": "indexed",
        "start_page": chunk.get("start_page"),
        "end_page": chunk.get("end_page"),
        "start_slide": chunk.get("start_slide"),
        "end_slide": chunk.get("end_slide"),
    }


def chunk_request_entry(row: dict, source_type: str) -> dict:
    """Map a persisted chunk row to the provider request entry."""
    is_presentation = source_type == "pptx"
    return {
        "chunk_index": row["chunk_index"],
        "text": row["text"],
        "normalized_text": row["normalized_text"],
        "token_count": row["token_count"],
        "start_segment": row["start_slide"] if is_presentation else row["start_page"],
        "end_segment": row["end_slide"] if is_presentation else row["end_page"],
        "metadata": row["tags"],
        "db_chunk_id": row["id"],
        "qdrant_point_id": row["qdrant_point_id"],
        "start_page": row["start_page"],
        "end_page": row["end_page"],
        "start_slide": row["start_slide"],
        "end_slide": row["end_slide"],
    }


def find_file_entry(entries: list[dict], *, course_id: str, relative_path: str) -> dict | None:
    for entry in entries:
        if entry.get("course_id") == course_id and entry.get("relative_path") == relative_path:
            return entry
    return None


def resume_entry(entries: list[dict], *, course_id: str, relative_path: str, sha256: str) -> dict | None:
    """Return the completed manifest entry a resumed run may skip, if any.

    Only files whose content hash still matches a completed entry are skipped;
    failed files are intentionally retried so a resumed run can converge.
    """
    entry = find_file_entry(entries, course_id=course_id, relative_path=relative_path)
    if entry is not None and entry.get("sha256") == sha256 and entry.get("status") == "complete":
        return entry
    return None


def upsert_file_entry(entries: list[dict], entry: dict) -> None:
    existing = find_file_entry(entries, course_id=entry["course_id"], relative_path=entry["relative_path"])
    if existing is None:
        entries.append(entry)
    else:
        existing.clear()
        existing.update(entry)


def load_manifest(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_manifest(path: Path, manifest: dict) -> None:
    """Atomically publish the manifest so a crash never leaves half a JSON file."""
    manifest["updated_at_utc"] = datetime.now(UTC).isoformat()
    body = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(body, encoding="utf-8")
    os.replace(temp, path)


def build_summary(
    *,
    database: Path,
    manifest_path: Path,
    course_count: int,
    selected_files: int,
    run_entries: list[dict],
    skipped_entries: list[dict],
    unsupported_skipped: int,
    empty_skipped: int,
    elapsed_wall_seconds: float,
) -> dict:
    failed = [entry for entry in run_entries if entry["status"] != "complete"]
    embedded = sum(int(entry.get("embedded_count") or 0) for entry in run_entries)
    indexed = sum(int(entry.get("indexed_count") or 0) for entry in run_entries)
    cost = sum(
        (Decimal(str(entry["embedding_cost_usd"])) for entry in run_entries if entry.get("embedding_cost_usd")),
        Decimal("0"),
    )
    return {
        "database": str(database),
        "manifest": str(manifest_path),
        "courses": course_count,
        "selected_files": selected_files,
        "complete": len(run_entries) - len(failed),
        "failed": len(failed),
        "failed_files": [entry["relative_path"] for entry in failed],
        "skipped_resume": len(skipped_entries),
        "unsupported_skipped": unsupported_skipped,
        "empty_skipped": empty_skipped,
        "chunks": sum(entry.get("chunk_count", 0) for entry in run_entries)
        + sum(entry.get("chunk_count", 0) for entry in skipped_entries),
        "embedded_chunks": embedded,
        "indexed_points": indexed,
        "embedding_cost_usd": str(cost),
        "ocr_completed_files": sum(entry.get("ocr_status") == "complete" for entry in run_entries),
        "deepseek_ocr_files": sum(entry.get("ocr_completed_provider") == "deepseek/deepseek-v4.1-flash"
                                   for entry in run_entries),
        "provider_known_cost_usd": str(sum((Decimal(str(entry.get("provider_known_cost_usd") or "0"))
                                            for entry in run_entries), Decimal("0"))),
        "elapsed_wall_seconds": elapsed_wall_seconds,
    }


# ---------------------------------------------------------------------------
# Child processes: per-file parse/normalize/chunk, and the embedding provider.
# ---------------------------------------------------------------------------


def parse_child(path: Path, *, enable_ocr: bool = False, enable_vision: bool = False) -> int:
    """Parse -> optional OCR/figure cards -> normalize -> chunk one file."""
    _prefer_worker_path()
    from app.ledger import load_ledger_guard
    from app.services.chunking import build_chunks
    from app.services.figures import scanned_page_ratio
    from app.services.normalization import normalize_document
    from app.services.ocr import run_ocr, should_run_ocr
    from app.services.parsing import parse_source_bytes
    from app.services.visual import enrich_with_figures
    from app.services.vision import build_vision_client
    from app.settings import settings

    started = time.perf_counter_ns()
    row: dict[str, Any] = {"path": str(path), "status": "failed", "chunks": [], "timings_ms": {}}
    stage = "read"
    guard = None
    try:
        guard = load_ledger_guard() if (enable_ocr or enable_vision) else None
        if (enable_ocr or enable_vision) and guard is None:
            raise RuntimeError("OCR/vision corpus build requires the shared spend ledger")
        stage_started = time.perf_counter_ns()
        raw = path.read_bytes()
        row["timings_ms"]["read_ms"] = elapsed_ms(stage_started)
        row["byte_size"] = len(raw)
        stage = "parse"
        stage_started = time.perf_counter_ns()
        try:
            parsed = parse_source_bytes(raw, path.name)
            parse_exc = None
        except Exception as exc:
            parse_exc = exc
            if path.suffix.lower() != ".pdf" or not enable_ocr:
                raise
            parsed = None
        row["timings_ms"]["parse_ms"] = elapsed_ms(stage_started)
        row["extraction_method"] = parsed.metadata.get("extraction_method") if parsed is not None else None
        row["requires_fallback_ocr"] = bool(parsed.metadata.get("requires_fallback_ocr")) if parsed is not None else True
        row["ocr_status"] = "not_requested" if not enable_ocr else "not_needed"
        if enable_ocr:
            trigger = parse_exc is not None or bool(parsed.metadata.get("requires_fallback_ocr")) \
                or should_run_ocr(parsed.text, raw, threshold=0.15) \
                or (enable_vision and (scanned_page_ratio(raw, path.name) or 0.0) >= 0.5)
            if trigger:
                stage = "ocr"
                stage_started = time.perf_counter_ns()
                row["ocr_events"] = []
                ocr_text = run_ocr(raw, filename=path.name,
                                   mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                                   guard=guard, telemetry=row["ocr_events"])
                parsed = parse_source_bytes(ocr_text.encode("utf-8"), f"{path.name}.ocr.txt")
                row["timings_ms"]["ocr_ms"] = elapsed_ms(stage_started)
                row["ocr_status"] = "complete"
                row["ocr_text_chars"] = len(ocr_text)
                row["ocr_completed_provider"] = "deepseek/deepseek-v4.1-flash" if any(
                    event.get("provider") == "openrouter" and event.get("status") == "complete"
                    for event in row["ocr_events"]) else "unstructured"
        if enable_vision:
            stage = "vision"
            stage_started = time.perf_counter_ns()
            settings.visual_enabled = True
            vision = build_vision_client(guard, environ={
                "CURRICULUM_TUTOR_OPENROUTER_API_KEY": settings.openrouter_api_key or "",
                "CURRICULUM_TUTOR_GEMINI_API_KEY": settings.gemini_api_key or "",
            })
            if vision is None:
                raise RuntimeError("configured figure-card vision client is unavailable")
            parsed, row["visual_summary"] = enrich_with_figures(
                parsed, raw, path.name, settings=settings, guard=guard, vision=vision)
            row["timings_ms"]["vision_ms"] = elapsed_ms(stage_started)
        stage = "normalize"
        stage_started = time.perf_counter_ns()
        normalized = normalize_document(parsed)
        row["timings_ms"]["normalize_ms"] = elapsed_ms(stage_started)
        row["text_chars"] = len(normalized.text)
        row["segment_count"] = len(normalized.segments)
        stage = "chunk"
        stage_started = time.perf_counter_ns()
        # Exact same chunk builder as run_ingestion_pipeline.
        chunks = build_chunks(normalized)
        row["timings_ms"]["chunk_ms"] = elapsed_ms(stage_started)
        if not chunks:
            raise ValueError("no chunks generated")
        row["chunks"] = [
            {
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "normalized_text": chunk.normalized_text,
                "token_count": chunk.token_count,
                "start_segment": chunk.start_segment,
                "end_segment": chunk.end_segment,
                "metadata": chunk.metadata,
            }
            for chunk in chunks
        ]
        row["chunk_count"] = len(chunks)
        row["chunk_tokens"] = sum(chunk.token_count for chunk in chunks)
        row["status"] = "parsed_and_chunked"
    except Exception as exc:  # noqa: BLE001 - failures are recorded per file, never fatal
        row["failed_stage"] = stage
        row["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    row["timings_ms"]["total_ms"] = elapsed_ms(started)
    if enable_ocr or enable_vision:
        row["provider_known_cost_usd"] = str(sum((call.actual_usd for call in guard.calls), Decimal("0"))) if guard else "0"
    print(json.dumps(row, ensure_ascii=True), flush=True)
    return 0 if row["status"] == "parsed_and_chunked" else 1


def run_parse_child(path: Path, *, timeout_seconds: int, enable_ocr: bool = False,
                    enable_vision: bool = False, env: dict[str, str] | None = None) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()), "--_file", str(path)]
    if enable_ocr:
        command.append("--ocr")
    if enable_vision:
        command.append("--vision")
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env={**os.environ, **(env or {}), "PYTHONIOENCODING": "utf-8"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "failed_stage": "timeout", "timings_ms": {},
                "error": f"exceeded {timeout_seconds}s per-file limit"}
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return {"status": "failed", "failed_stage": "child", "timings_ms": {},
                "error": f"child produced no result (exit {completed.returncode})"}
    try:
        parsed = json.loads(stdout.splitlines()[-1])
    except ValueError:
        return {"status": "failed", "failed_stage": "child", "timings_ms": {},
                "error": "child result was not JSON"}
    return parsed


def _load_provider_guard():
    from app.ledger import INGESTION_EMBEDDING_OPERATION, load_ledger_guard
    from app.settings import settings

    if not settings.openrouter_api_key:
        raise RuntimeError("CURRICULUM_TUTOR_OPENROUTER_API_KEY is required for OpenRouter embeddings")
    guard = load_ledger_guard()
    if guard is None:
        raise RuntimeError("EDUGROUND_EVAL_BUDGET_LEDGER is required for embedding")
    guard.schedule(provider="openrouter", model=settings.openrouter_embedding_model,
                   operation=INGESTION_EMBEDDING_OPERATION)
    return guard


def build_provider_points(request: dict, vectors: list[list[float]]) -> list[dict]:
    """Worker point builder + the runtime job payload fields the API reads."""
    from app.services.indexing import build_qdrant_point
    from app.services.types import ChunkRecord

    scope = request["scope"]
    points: list[dict] = []
    for position, chunk in enumerate(request["chunks"]):
        record = ChunkRecord(
            chunk_index=int(chunk["chunk_index"]),
            text=chunk["text"],
            normalized_text=chunk["normalized_text"],
            token_count=int(chunk["token_count"]),
            start_segment=chunk.get("start_segment"),
            end_segment=chunk.get("end_segment"),
            metadata=chunk.get("metadata") or {},
        )
        point = build_qdrant_point(
            record,
            vectors[position],
            notebook_id=scope["notebook_id"],
            institution_id=scope["institution_id"],
            source_id=scope["source_id"],
            source_version_id=scope["source_version_id"],
            module_id=scope.get("module_id"),
        )
        point["payload"].update(runtime_payload_fields(chunk))
        if point["id"] != chunk["qdrant_point_id"]:
            raise RuntimeError(f"qdrant point id mismatch for chunk {chunk['chunk_index']}")
        points.append(point)
    return points


def provider_main() -> int:
    """Hold ONE ledger guard for the run and answer embed/index JSON-line requests."""
    _prefer_worker_path()
    from app.services.embeddings import embed_texts

    guard = None
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        try:
            request = json.loads(raw_line)
        except ValueError:
            break
        stage = "preflight"
        response: dict[str, Any]
        try:
            if request.get("kind") == "preflight":
                guard = guard or _load_provider_guard()
                from app.settings import settings

                response = {
                    "ok": True,
                    "model": settings.openrouter_embedding_model,
                    "ledger": str(guard.ledger.path),
                    "cap_usd": str(guard.ledger.cap_usd),
                }
            elif request.get("kind") == "embed_index":
                if guard is None:
                    raise RuntimeError("embed_index requires a preflight request first")
                chunks = request["chunks"]
                batch_size = int(request.get("batch_size") or 64)
                calls_before = len(guard.calls)
                stage = "embed"
                stage_started = time.perf_counter_ns()
                vectors: list[list[float]] = []
                for start in range(0, len(chunks), batch_size):
                    batch = chunks[start:start + batch_size]
                    batch_vectors = embed_texts([chunk["text"] for chunk in batch], guard=guard)
                    if len(batch_vectors) != len(batch):
                        raise RuntimeError(
                            f"embedding count mismatch: expected {len(batch)}, got {len(batch_vectors)}"
                        )
                    vectors.extend(batch_vectors)
                embed_ms = elapsed_ms(stage_started)
                indexed = 0
                index_ms = None
                if request.get("index"):
                    stage = "index"
                    from app.services.vectorstore import ensure_collection, upsert_points

                    stage_started = time.perf_counter_ns()
                    points = build_provider_points(request, vectors)
                    ensure_collection(len(vectors[0]))
                    upsert_points(points)
                    indexed = len(points)
                    index_ms = elapsed_ms(stage_started)
                cost = sum((call.actual_usd for call in guard.calls[calls_before:]), Decimal("0"))
                response = {
                    "ok": True,
                    "embedded_count": len(vectors),
                    "indexed_count": indexed,
                    "dimension": len(vectors[0]) if vectors else None,
                    "embed_ms": embed_ms,
                    "index_ms": index_ms,
                    "cost_usd": str(cost),
                }
            elif request.get("kind") == "state":
                if guard is None:
                    raise RuntimeError("state requires a preflight request first")
                state = guard.ledger.state()
                response = {
                    "ok": True,
                    "cap_usd": str(state.cap_usd),
                    "reserved_usd": str(state.reserved_usd),
                    "settled_usd": str(state.settled_usd),
                    "committed_usd": str(state.committed_usd),
                    "unknown_attempts": state.unknown_attempts,
                }
            else:
                raise ValueError(f"unknown request kind {request.get('kind')!r}")
        except Exception as exc:  # noqa: BLE001 - report per-request failure, keep serving
            response = {"ok": False, "stage": stage, "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
        print(json.dumps(response, ensure_ascii=True), flush=True)
    return 0


class EmbeddingBridge:
    """Persistent worker-side child holding the one budget guard for the run."""

    def __init__(self, *, env: dict[str, str]) -> None:
        command = [sys.executable, str(Path(__file__).resolve()), "--_provider"]
        self._process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def request(self, payload: dict) -> dict:
        if self._process.poll() is not None or self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError(f"embedding provider exited with code {self._process.returncode}")
        try:
            self._process.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
            self._process.stdin.flush()
        except OSError as exc:
            raise RuntimeError(f"embedding provider pipe failed: {exc}") from exc
        line = self._process.stdout.readline()
        if not line:
            raise RuntimeError(f"embedding provider produced no response (exit {self._process.poll()})")
        return json.loads(line)

    def close(self) -> None:
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
            self._process.wait(timeout=10)
        except Exception:  # noqa: BLE001 - best-effort cleanup of the child
            self._process.kill()


# ---------------------------------------------------------------------------
# Parent: environment bootstrap, catalog rows and per-file persistence.
# ---------------------------------------------------------------------------


def configure_local_environment(database_path: Path) -> None:
    """Force the API app onto local SQLite before any app module import.

    Mirrors tools/evaluation/run_local_workflow_golden_qa.configure_local_environment.
    """
    database_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_url = f"sqlite:///{database_path.as_posix()}"
    os.environ["CURRICULUM_TUTOR_DATABASE_URL"] = sqlite_url
    os.environ["DATABASE_URL"] = sqlite_url
    os.environ.setdefault("CURRICULUM_TUTOR_CLERK_ENABLED", "false")
    os.environ.setdefault("CURRICULUM_TUTOR_AUTO_CREATE_SCHEMA", "true")
    os.environ.setdefault("CURRICULUM_TUTOR_REDIS_URL", "redis://localhost:0/0")


def ensure_catalog(session, manifest: dict, courses: list[dict]) -> None:
    """Create the institution/user/notebook rows the read-side tools need."""
    from app.models.curriculum import Notebook, NotebookMembership
    from app.models.enums import (
        AuthProvider,
        DeploymentMode,
        InstitutionRole,
        NotebookMembershipRole,
        NotebookVisibility,
        PolicyMode,
        PrivacyMode,
        UserStatus,
    )
    from app.models.identity import Institution, InstitutionMembership, User

    now = datetime.now(UTC)
    institution_id = manifest.get("institution_id") or str(uuid4())
    user_id = manifest.get("user_id") or str(uuid4())
    if session.get(Institution, institution_id) is None:
        session.add(
            Institution(
                id=institution_id,
                name="Eduground Local Corpus",
                slug=f"local-corpus-{institution_id[:8]}",
                deployment_mode=DeploymentMode.CLOUD,
                privacy_mode=PrivacyMode.STANDARD,
            )
        )
    if session.get(User, user_id) is None:
        session.add(
            User(
                id=user_id,
                institution_id=institution_id,
                email=f"local-corpus-{user_id[:8]}@eduground.local",
                auth_provider=AuthProvider.LOCAL,
                external_subject_id=f"local-corpus-{user_id}",
                display_name="Local Corpus Builder",
                status=UserStatus.ACTIVE,
            )
        )
    if (
        session.query(InstitutionMembership)
        .filter(InstitutionMembership.institution_id == institution_id, InstitutionMembership.user_id == user_id)
        .one_or_none()
        is None
    ):
        session.add(
            InstitutionMembership(institution_id=institution_id, user_id=user_id, role=InstitutionRole.INSTRUCTOR,
                                  created_at=now)
        )
    manifest.setdefault("courses", {})
    for course in courses:
        course_id = course["course_id"]
        recorded = manifest["courses"].get(course_id) or {}
        notebook_id = recorded.get("notebook_id") or str(uuid4())
        if session.get(Notebook, notebook_id) is None:
            session.add(
                Notebook(
                    id=notebook_id,
                    institution_id=institution_id,
                    course_offering_id=None,
                    title=(course.get("title") or course_id)[:255],
                    description=f"Local corpus build for {course.get('relative_directory') or course_id}",
                    owner_user_id=user_id,
                    visibility=NotebookVisibility.PRIVATE,
                    policy_mode=PolicyMode.TEACHING,
                )
            )
        if (
            session.query(NotebookMembership)
            .filter(NotebookMembership.notebook_id == notebook_id, NotebookMembership.user_id == user_id)
            .one_or_none()
            is None
        ):
            session.add(
                NotebookMembership(notebook_id=notebook_id, user_id=user_id, role=NotebookMembershipRole.OWNER,
                                   granted_by_user_id=user_id, created_at=now)
            )
        manifest["courses"][course_id] = {
            "title": course.get("title") or course_id,
            "relative_directory": course.get("relative_directory"),
            "notebook_id": notebook_id,
        }
    manifest["institution_id"] = institution_id
    manifest["user_id"] = user_id
    session.commit()


def purge_source(session, source_id: str | None) -> None:
    """Remove a previous attempt for one source so retries stay idempotent."""
    if not source_id:
        return
    from app.models.content import Chunk, IngestionJob, Source, SourceVersion

    session.query(Chunk).filter(Chunk.source_id == source_id).delete(synchronize_session=False)
    session.query(IngestionJob).filter(IngestionJob.source_id == source_id).delete(synchronize_session=False)
    session.query(SourceVersion).filter(SourceVersion.source_id == source_id).delete(synchronize_session=False)
    session.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
    session.flush()


def purge_notebook_checksum(session, notebook_id: str, checksum_sha256: str) -> None:
    """Keep one indexed source per checksum in a notebook (evidence export requires it)."""
    from app.models.content import Source

    source_ids = [
        row[0]
        for row in session.query(Source.id)
        .filter(Source.notebook_id == notebook_id, Source.checksum_sha256 == checksum_sha256)
        .all()
    ]
    for source_id in source_ids:
        purge_source(session, source_id)


def persist_file(session, manifest: dict, item: dict, child_row: dict, *, user_id: str) -> dict:
    """Write Source/SourceVersion/Chunk/IngestionJob rows for one parsed file."""
    from app.models.content import Chunk, IngestionJob, Source, SourceVersion
    from app.models.enums import IngestionJobType, JobStatus, SourceOrigin, SourceStatus, SourceType

    course_id = item["course_id"]
    relative_path = item["relative_path"]
    notebook_id = manifest["courses"][course_id]["notebook_id"]
    now = datetime.now(UTC)

    previous = find_file_entry(manifest["files"], course_id=course_id, relative_path=relative_path)
    if previous is not None:
        purge_source(session, previous.get("source_id"))
    purge_notebook_checksum(session, notebook_id, item["sha256"])

    source_id = str(uuid4())
    source_version_id = str(uuid4())
    job_id = str(uuid4())
    storage_key = f"local-corpus/{course_id}/{relative_path}"[:512]
    session.add(
        Source(
            id=source_id,
            notebook_id=notebook_id,
            module_id=None,
            source_type=SourceType(SOURCE_TYPE_BY_SUFFIX[item["suffix"]]),
            title=(Path(relative_path).stem or Path(relative_path).name)[:255],
            original_filename=Path(relative_path).name[:255],
            storage_key=storage_key,
            mime_type=mimetypes.guess_type(relative_path)[0] or "application/octet-stream",
            checksum_sha256=item["sha256"],
            byte_size=int(item["byte_size"]),
            language_code="en",
            version_number=1,
            status=SourceStatus.INDEXED,
            source_origin=SourceOrigin.UPLOAD,
            created_by_user_id=user_id,
        )
    )
    session.add(
        SourceVersion(
            id=source_version_id,
            source_id=source_id,
            version_number=1,
            storage_key=storage_key,
            checksum_sha256=item["sha256"],
            status=SourceStatus.INDEXED.value,
            created_at=now,
            created_by_user_id=user_id,
        )
    )
    chunk_rows = build_chunk_rows(
        source_id=source_id,
        source_version_id=source_version_id,
        module_id=None,
        source_type=item["source_type"],
        chunks=child_row["chunks"],
    )
    for row in chunk_rows:
        session.add(Chunk(**row))
    session.add(
        IngestionJob(
            id=job_id,
            source_id=source_id,
            source_version_id=source_version_id,
            job_type=IngestionJobType.PARSE,
            status=JobStatus.COMPLETED,
            attempt_count=1,
            stage="completed",
            storage_key_snapshot=storage_key,
            progress_json={"extraction_method": child_row.get("extraction_method"), "local_corpus_build": True},
            segment_count=int(child_row.get("segment_count") or 0),
            chunk_count=len(chunk_rows),
            embedded_count=0,
            indexed_count=0,
            started_at=now,
            finished_at=now,
            created_at=now,
        )
    )
    session.commit()
    return {"source_id": source_id, "source_version_id": source_version_id, "job_id": job_id,
            "chunk_rows": chunk_rows}


def process_child_result(
    session,
    manifest: dict,
    item: dict,
    child_row: dict,
    *,
    user_id: str,
    args: argparse.Namespace,
    bridge: EmbeddingBridge | None,
    embedding_blocked: bool,
    submitted_at: int,
) -> tuple[dict, bool]:
    """Persist one file result and optionally embed/index it."""
    from app.models.content import IngestionJob

    entry: dict[str, Any] = {
        "course_id": item["course_id"],
        "relative_path": item["relative_path"],
        "sha256": item["sha256"],
        "byte_size": item["byte_size"],
        "suffix": item["suffix"],
        "source_type": item["source_type"],
        "status": "parse_failed",
        "source_id": None,
        "source_version_id": None,
        "chunk_count": 0,
        "timings_ms": dict(child_row.get("timings_ms") or {}),
        "process_wall_ms": elapsed_ms(submitted_at),
        "error": None,
        "ocr_status": child_row.get("ocr_status"),
        "ocr_completed_provider": child_row.get("ocr_completed_provider"),
        "ocr_events": child_row.get("ocr_events"),
        "visual_summary": child_row.get("visual_summary"),
        "provider_known_cost_usd": child_row.get("provider_known_cost_usd"),
    }
    if child_row.get("status") != "parsed_and_chunked":
        entry["error"] = str(child_row.get("error") or "parse/chunk failed")
        entry["timings_ms"]["failed_stage"] = child_row.get("failed_stage") or "unknown"
        upsert_file_entry(manifest["files"], entry)
        return entry, embedding_blocked

    stage_started = time.perf_counter_ns()
    try:
        persisted = persist_file(session, manifest, item, child_row, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - isolate per-file persistence failures
        session.rollback()
        entry["status"] = "persist_failed"
        entry["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        upsert_file_entry(manifest["files"], entry)
        return entry, embedding_blocked
    entry["timings_ms"]["persist_ms"] = elapsed_ms(stage_started)
    entry.update(
        source_id=persisted["source_id"],
        source_version_id=persisted["source_version_id"],
        chunk_count=len(persisted["chunk_rows"]),
        chunk_tokens=sum(row["token_count"] for row in persisted["chunk_rows"]),
    )

    if bridge is None:
        entry.update(status="complete", embedding_status="not_requested", index_status="not_requested")
    elif embedding_blocked:
        entry.update(status="complete", embedding_status="skipped_after_prior_failure",
                     index_status="skipped_after_prior_failure")
    else:
        request = {
            "kind": "embed_index",
            "batch_size": args.embed_batch,
            "index": bool(args.index),
            "collection": args.collection if args.index else None,
            "scope": {
                "notebook_id": manifest["courses"][item["course_id"]]["notebook_id"],
                "institution_id": manifest["institution_id"],
                "source_id": persisted["source_id"],
                "source_version_id": persisted["source_version_id"],
                "module_id": None,
            },
            "chunks": [chunk_request_entry(row, item["source_type"]) for row in persisted["chunk_rows"]],
        }
        stage_started = time.perf_counter_ns()
        try:
            response = bridge.request(request)
        except Exception as exc:  # noqa: BLE001 - a dead provider blocks further embedding
            response = {"ok": False, "stage": "embed", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
        entry["timings_ms"]["embed_request_ms"] = elapsed_ms(stage_started)
        if response.get("ok"):
            embedded = int(response.get("embedded_count") or 0)
            indexed = int(response.get("indexed_count") or 0)
            entry.update(
                status="complete",
                embedding_status="complete",
                embedded_count=embedded,
                index_status="complete" if args.index else "not_requested",
                indexed_count=indexed,
                embedding_dimension=response.get("dimension"),
                embedding_cost_usd=response.get("cost_usd"),
            )
            entry["timings_ms"]["embed_ms"] = response.get("embed_ms")
            entry["timings_ms"]["index_ms"] = response.get("index_ms")
            job = session.get(IngestionJob, persisted["job_id"])
            if job is not None:
                job.embedded_count = embedded
                job.indexed_count = indexed
                session.commit()
        else:
            stage = str(response.get("stage") or "embed")
            entry["status"] = "index_failed" if stage == "index" else "embedding_failed"
            entry["embedding_status"] = "failed"
            entry["error"] = str(response.get("error") or "embedding provider failed")
            embedding_blocked = stage == "embed"

    upsert_file_entry(manifest["files"], entry)
    return entry, embedding_blocked


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--courses", type=Path, default=ROOT / "Courses")
    parser.add_argument("--db", type=Path, help="SQLite output (default evaluation/local_corpus/corpus.sqlite3)")
    parser.add_argument("--manifest-out", type=Path,
                        help="manifest directory (default timestamped under evaluation/local_corpus)")
    parser.add_argument("--workers", type=int, default=8, help="concurrent per-file child processes")
    parser.add_argument("--embed", action=argparse.BooleanOptionalAction, default=False,
                        help="live OpenRouter embeddings through the shared ledger")
    parser.add_argument("--ocr", action="store_true",
                        help="Unstructured OCR with optional DeepSeek V4.1 Flash page fallback")
    parser.add_argument("--vision", action="store_true",
                        help="include configured figure-card vision enrichment")
    parser.add_argument("--embed-batch", type=int, default=64, help="chunks per embedding request")
    parser.add_argument("--ledger", type=Path, default=ROOT / "evaluation" / "pilot" / "budget.sqlite3")
    parser.add_argument("--prices", type=Path, default=ROOT / "evaluation" / "prices" / "2026-09-30.openrouter.json")
    parser.add_argument("--index", action=argparse.BooleanOptionalAction, default=False,
                        help="upsert Qdrant points (requires --embed)")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--limit-courses", type=int)
    parser.add_argument("--limit-files", type=int)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True,
                        help="skip files already completed in the manifest")
    parser.add_argument("--_file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_preflight", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_provider", action="store_true", help=argparse.SUPPRESS)
    return parser


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is optional for no-embed runs
        return
    load_dotenv(ROOT / ".env", override=False)


def preflight_live_child(args: argparse.Namespace) -> int:
    """Validate worker-side pricing in another process to avoid API/worker app import collision."""
    _prefer_worker_path()
    from app.ledger import load_ledger_guard
    from app.settings import settings

    try:
        guard = load_ledger_guard()
        if guard is None:
            raise ValueError("shared evaluation ledger is missing")
        if args.ocr:
            if settings.unstructured_api_key:
                guard.schedule(provider="unstructured", model="hosted-api", operation="ocr")
            if settings.ocr_vision_enabled:
                if settings.ocr_vision_model != "deepseek/deepseek-v4.1-flash" or not settings.openrouter_api_key:
                    raise ValueError("DeepSeek V4.1 Flash OCR fallback requires its pinned model and OpenRouter key")
                guard.schedule(provider="openrouter", model=settings.ocr_vision_model, operation="ocr_vision")
            elif not settings.unstructured_api_key:
                raise ValueError("OCR requires Unstructured or enabled DeepSeek fallback")
        if args.vision:
            if not settings.visual_provider or not settings.visual_model:
                raise ValueError("figure-card vision provider/model must be pinned")
            if bool(settings.visual_fallback_provider) != bool(settings.visual_fallback_model):
                raise ValueError("figure-card fallback needs both provider and model")
            for provider, model in ((settings.visual_provider, settings.visual_model),
                                    (settings.visual_fallback_provider, settings.visual_fallback_model)):
                if provider and model:
                    key = settings.openrouter_api_key if provider == "openrouter" else settings.gemini_api_key
                    if provider not in {"openrouter", "gemini"} or not key:
                        raise ValueError(f"figure-card {provider} key/provider is unavailable")
                    guard.schedule(provider=provider, model=model, operation="visual_understanding")
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


def shared_ledger_snapshot(path: Path) -> dict | None:
    """Read OCR/vision/embedding totals without importing the worker app in the API parent."""
    if not path.is_file():
        return None
    with sqlite3.connect(path) as db:
        cap_row = db.execute("SELECT cap_usd FROM budget_meta").fetchone()
        rows = db.execute("SELECT role,reserved_usd,actual_usd,status FROM calls").fetchall()
    settled = Decimal("0")
    reserved = Decimal("0")
    unknown = 0
    by_role: dict[str, Decimal] = {}
    for role, estimate, actual, status in rows:
        if status == "not_dispatched":
            continue
        amount = Decimal(actual) if status == "settled" and actual is not None else Decimal(estimate)
        if status == "settled":
            settled += amount
        else:
            reserved += amount
        unknown += status == "unknown"
        by_role[role] = by_role.get(role, Decimal("0")) + amount
    return {"cap_usd": cap_row[0] if cap_row else None, "settled_usd": str(settled),
            "reserved_usd": str(reserved), "committed_usd": str(settled + reserved),
            "unknown_attempts": unknown,
            "by_role_usd": {role: str(amount) for role, amount in by_role.items()}}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args._file is not None:
        return parse_child(args._file, enable_ocr=args.ocr, enable_vision=args.vision)
    if args._preflight:
        return preflight_live_child(args)
    if args._provider:
        return provider_main()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.workers < 1:
        parser.error("--workers must be >=1")
    if not 1 <= args.embed_batch <= 512:
        parser.error("--embed-batch must be between 1 and 512")
    if args.index and not args.embed:
        parser.error("--index requires --embed (points need vectors)")
    if args.limit_courses is not None and args.limit_courses < 1:
        parser.error("--limit-courses must be >=1")
    if args.limit_files is not None and args.limit_files < 0:
        parser.error("--limit-files must be >=0")
    if args.embed or args.ocr or args.vision:
        if not args.prices.is_file():
            parser.error(f"dated price registry missing: {args.prices}")
        _load_dotenv()
        if args.embed and not os.environ.get("CURRICULUM_TUTOR_OPENROUTER_API_KEY", "").strip():
            parser.error("OpenRouter embedding key is missing from environment/.env")
    live_env = None
    if args.ocr or args.vision:
        live_env = {"EDUGROUND_EVAL_BUDGET_LEDGER": str(args.ledger.resolve()),
                    "EDUGROUND_EVAL_MAX_API_SPEND_USD": SPEND_CAP_USD,
                    "EDUGROUND_EVAL_PRICE_REGISTRY": str(args.prices.resolve())}
        command = [sys.executable, str(Path(__file__).resolve()), "--_preflight"]
        if args.ocr:
            command.append("--ocr")
        if args.vision:
            command.append("--vision")
        checked = subprocess.run(command, cwd=ROOT, env={**os.environ, **live_env},
                                 capture_output=True, text=True, timeout=30, check=False)
        if checked.returncode:
            parser.error(f"OCR/vision preflight failed: {checked.stderr.strip()[:500]}")

    try:
        inventory = inventory_courses(args.courses)
    except Exception as exc:  # noqa: BLE001 - bad registry/tree is a hard stop
        parser.error(f"course inventory failed: {exc}")
    courses = inventory["courses"]
    if args.limit_courses is not None:
        courses = courses[: args.limit_courses]
    selected = {course["course_id"] for course in courses}
    targets: list[dict] = []
    unsupported_skipped = 0
    empty_skipped = 0
    for document in inventory["documents"]:
        if document["course_id"] not in selected:
            continue
        if args.limit_files is not None and len(targets) >= args.limit_files:
            break
        if document["byte_size"] == 0:
            empty_skipped += 1
            continue
        if not document["supported"]:
            unsupported_skipped += 1
            continue
        targets.append(document)
    logger.info(
        "Inventory: %d course(s), %d file(s) selected (%d unsupported, %d empty skipped)",
        len(courses), len(targets), unsupported_skipped, empty_skipped,
    )

    if args.db is None:
        args.db = ROOT / "evaluation" / "local_corpus" / "corpus.sqlite3"
    if args.manifest_out is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        args.manifest_out = ROOT / "evaluation" / "local_corpus" / f"manifest-{stamp}-{os.getpid()}"
    args.manifest_out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.manifest_out / MANIFEST_FILENAME
    manifest = load_manifest(manifest_path) if args.resume else None
    if manifest is None:
        manifest = {
            "schema_version": 1,
            "kind": "local_corpus_build",
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "files": [],
            "courses": {},
        }
    else:
        manifest.setdefault("files", [])
        manifest.setdefault("courses", {})
        if bool(manifest.get("ocr_enabled", False)) != bool(args.ocr) or \
                bool(manifest.get("vision_enabled", False)) != bool(args.vision):
            parser.error("resume manifest OCR/vision modes differ; choose a new --manifest-out")
        logger.info("Resuming manifest with %d recorded file(s)", len(manifest["files"]))
    manifest.update(
        {
            "courses_root": str(args.courses.resolve()),
            "database": str(args.db),
            "embed_enabled": bool(args.embed),
            "ocr_enabled": bool(args.ocr),
            "vision_enabled": bool(args.vision),
            "index_enabled": bool(args.index),
            "collection": args.collection if args.index else None,
            "embedding_model": None,
            "ledger": None,
        }
    )
    write_manifest(manifest_path, manifest)

    bridge: EmbeddingBridge | None = None
    if args.embed:
        bridge_env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "EDUGROUND_EVAL_BUDGET_LEDGER": str(args.ledger.resolve()),
            "EDUGROUND_EVAL_MAX_API_SPEND_USD": SPEND_CAP_USD,
            "EDUGROUND_EVAL_PRICE_REGISTRY": str(args.prices.resolve()),
        }
        if args.index:
            bridge_env["CURRICULUM_TUTOR_QDRANT_COLLECTION_NAME"] = args.collection
        bridge = EmbeddingBridge(env=bridge_env)
        try:
            preflight = bridge.request({"kind": "preflight"})
        except Exception as exc:  # noqa: BLE001 - misconfigured provider must stop the run
            bridge.close()
            parser.error(f"embedding preflight failed: {exc}")
        if not preflight.get("ok"):
            bridge.close()
            parser.error(f"embedding preflight failed: {preflight.get('error')}")
        manifest["embedding_model"] = preflight.get("model")
        manifest["ledger"] = preflight.get("ledger")
        logger.info("Embedding provider ready: %s (ledger %s)", preflight.get("model"), preflight.get("ledger"))

    configure_local_environment(args.db)
    _prefer_api_path()

    started_wall = time.monotonic()
    run_entries: list[dict] = []
    skipped_entries: list[dict] = []
    ledger_state: dict | None = None
    summary: dict | None = None
    try:
        from app.db import init_db  # noqa: F401 - registers every model on Base.metadata
        from app.db.base import Base
        from app.db.session import SessionLocal, engine

        Base.metadata.create_all(bind=engine)
        with SessionLocal() as session:
            ensure_catalog(session, manifest, courses)
            write_manifest(manifest_path, manifest)
            from app.models.content import Source

            pending: list[dict] = []
            for item in targets:
                entry = (
                    resume_entry(manifest["files"], course_id=item["course_id"],
                                 relative_path=item["relative_path"], sha256=item["sha256"])
                    if args.resume
                    else None
                )
                if entry is not None and session.get(Source, entry["source_id"]) is not None:
                    skipped_entries.append(entry)
                    continue
                pending.append(item)
            if skipped_entries:
                logger.info("Resume: %d file(s) already complete", len(skipped_entries))

            embedding_blocked = False
            processed = 0
            if pending:
                with ThreadPoolExecutor(max_workers=args.workers) as executor:
                    futures = {}
                    for item in pending:
                        submitted = time.perf_counter_ns()
                        path = args.courses.resolve() / item["relative_path"]
                        futures[executor.submit(run_parse_child, path, timeout_seconds=FILE_TIMEOUT_SECONDS,
                                                enable_ocr=args.ocr, enable_vision=args.vision,
                                                env=live_env)] = (
                            item, submitted,
                        )
                    for future in as_completed(futures):
                        item, submitted = futures[future]
                        processed += 1
                        try:
                            child_row = future.result()
                        except Exception as exc:  # noqa: BLE001 - isolate per-file child failures
                            child_row = {"status": "failed", "failed_stage": "child", "timings_ms": {},
                                         "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
                        entry, embedding_blocked = process_child_result(
                            session, manifest, item, child_row,
                            user_id=manifest["user_id"], args=args, bridge=bridge,
                            embedding_blocked=embedding_blocked, submitted_at=submitted,
                        )
                        run_entries.append(entry)
                        write_manifest(manifest_path, manifest)
                        suffix = f" | {entry['error']}" if entry.get("error") else ""
                        logger.info(
                            "[%d/%d] %s | %s | %s | chunks=%d wall=%.0fms%s",
                            processed, len(pending), item["course_id"], item["relative_path"],
                            entry["status"], entry["chunk_count"], entry["process_wall_ms"], suffix,
                        )
            summary = build_summary(
                database=args.db,
                manifest_path=manifest_path,
                course_count=len(courses),
                selected_files=len(targets),
                run_entries=run_entries,
                skipped_entries=skipped_entries,
                unsupported_skipped=unsupported_skipped,
                empty_skipped=empty_skipped,
                elapsed_wall_seconds=round(time.monotonic() - started_wall, 3),
            )
            manifest["summary"] = summary
            write_manifest(manifest_path, manifest)
    finally:
        if bridge is not None:
            try:
                state = bridge.request({"kind": "state"})
                if state.get("ok"):
                    ledger_state = state
            except Exception:  # noqa: BLE001 - ledger state is evidence, never fatal
                ledger_state = None
            bridge.close()

    if summary is None:  # pragma: no cover - only when the run aborted before summary
        return 1
    if args.embed or args.ocr or args.vision:
        ledger_state = shared_ledger_snapshot(args.ledger)
    summary["ledger"] = ledger_state
    manifest["summary"]["ledger"] = ledger_state
    write_manifest(manifest_path, manifest)
    exit_code = 1 if summary["failed"] else 0
    print(json.dumps({**summary, "exit_code": exit_code}, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
