from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Json
except ModuleNotFoundError:  # pragma: no cover - allows unit tests without psycopg installed
    psycopg = None
    dict_row = None

    def Json(value):
        return value

from app.settings import settings

JOB_STATUS_QUEUED = "QUEUED"
JOB_STATUS_RUNNING = "RUNNING"
JOB_STATUS_COMPLETED = "COMPLETED"
JOB_STATUS_FAILED = "FAILED"
SOURCE_STATUS_INDEXED = "INDEXED"
SOURCE_STATUS_FAILED = "FAILED"
SOURCE_STATUS_ARCHIVED = "ARCHIVED"
SEGMENT_TYPE_PAGE = "PAGE"
SEGMENT_TYPE_SLIDE = "SLIDE"
SEGMENT_TYPE_SECTION = "SECTION"
SEGMENT_TYPE_TIME_RANGE = "TIME_RANGE"


@dataclass(slots=True)
class IngestionContext:
    job_id: str
    source_id: str
    source_version_id: str
    job_type: str
    notebook_id: str
    institution_id: str
    module_id: str | None
    storage_key: str
    mime_type: str
    original_filename: str | None
    title: str
    source_type: str
    checksum_sha256: str
    source_status: str
    source_version_number: int


@contextmanager
def connect():
    if psycopg is None:
        raise RuntimeError("psycopg is required for worker database access")
    with psycopg.connect(_normalize_psycopg_conninfo(settings.database_url), row_factory=dict_row) as connection:
        yield connection


def _normalize_psycopg_conninfo(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+psycopg://")
    if database_url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + database_url.removeprefix("postgresql+psycopg2://")
    return database_url


def _normalize_segment_type(segment_type: str) -> str:
    mapping = {
        "page": SEGMENT_TYPE_PAGE,
        "slide": SEGMENT_TYPE_SLIDE,
        "section": SEGMENT_TYPE_SECTION,
        "time_range": SEGMENT_TYPE_TIME_RANGE,
    }
    return mapping.get(segment_type, SEGMENT_TYPE_SECTION)


def _strip_nul_bytes(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\x00", "")


def _sanitize_json_value(value):
    if isinstance(value, str):
        return _strip_nul_bytes(value)
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_json_value(item) for key, item in value.items()}
    return value


def _build_qdrant_point_id(source_version_id: str, chunk_index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"{source_version_id}:{chunk_index}"))


def load_ingestion_context(*, job_id: str, source_id: str, source_version_id: str, institution_id: str) -> IngestionContext:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                jobs.id AS job_id,
                jobs.source_id,
                jobs.source_version_id,
                jobs.job_type,
                sources.notebook_id,
                sources.module_id,
                sources.status AS source_status,
                versions.storage_key,
                sources.mime_type,
                sources.original_filename,
                sources.title,
                sources.source_type,
                versions.checksum_sha256,
                versions.version_number AS source_version_number
            FROM ingestion_jobs AS jobs
            JOIN sources ON sources.id = jobs.source_id
            JOIN source_versions AS versions ON versions.id = jobs.source_version_id
            WHERE jobs.id = %s AND jobs.source_id = %s AND jobs.source_version_id = %s
            """,
            (job_id, source_id, source_version_id),
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"Could not load ingestion context for job {job_id}")
    return IngestionContext(
        job_id=row["job_id"],
        source_id=row["source_id"],
        source_version_id=row["source_version_id"],
        job_type=row["job_type"],
        notebook_id=row["notebook_id"],
        institution_id=institution_id,
        module_id=row["module_id"],
        source_status=row["source_status"],
        storage_key=row["storage_key"],
        mime_type=row["mime_type"],
        original_filename=row["original_filename"],
        title=row["title"],
        source_type=row["source_type"],
        checksum_sha256=row["checksum_sha256"],
        source_version_number=row["source_version_number"],
    )


def mark_job_running(*, job_id: str) -> None:
    now = datetime.now(UTC)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE ingestion_jobs
            SET status = %s, stage = 'download', started_at = COALESCE(started_at, %s), attempt_count = attempt_count + 1,
                error_code = NULL, error_message = NULL, finished_at = NULL
            WHERE id = %s
            """,
            (JOB_STATUS_RUNNING, now, job_id),
        )
        connection.commit()


def update_job_stage(*, job_id: str, stage: str, progress_json: dict | None = None, segment_count: int | None = None, chunk_count: int | None = None, embedded_count: int | None = None, indexed_count: int | None = None) -> None:
    assignments = ["stage = %s"]
    values: list[object] = [stage]
    if progress_json is not None:
        assignments.append("progress_json = %s")
        values.append(Json(progress_json))
    if segment_count is not None:
        assignments.append("segment_count = %s")
        values.append(segment_count)
    if chunk_count is not None:
        assignments.append("chunk_count = %s")
        values.append(chunk_count)
    if embedded_count is not None:
        assignments.append("embedded_count = %s")
        values.append(embedded_count)
    if indexed_count is not None:
        assignments.append("indexed_count = %s")
        values.append(indexed_count)
    values.append(job_id)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(f"UPDATE ingestion_jobs SET {', '.join(assignments)} WHERE id = %s", values)
        connection.commit()


def replace_segments_and_chunks(
    *,
    context: IngestionContext,
    document,
    chunks,
) -> list[dict]:
    segment_rows: list[dict] = []
    chunk_rows: list[dict] = []
    now = datetime.now(UTC)
    for segment in document.segments:
        segment_type = _normalize_segment_type(segment.segment_type)
        segment_rows.append(
            {
                "id": str(uuid4()),
                "source_id": context.source_id,
                "source_version_id": context.source_version_id,
                "segment_type": segment_type,
                "segment_number": segment.segment_number,
                "title": _strip_nul_bytes(segment.title),
                "created_at": now,
            }
        )
    is_presentation = context.source_type == "pptx"
    for chunk in chunks:
        start_page = None if is_presentation else chunk.start_segment
        end_page = None if is_presentation else chunk.end_segment
        start_slide = chunk.start_segment if is_presentation else None
        end_slide = chunk.end_segment if is_presentation else None
        chunk_rows.append(
            {
                "id": str(uuid4()),
                "source_id": context.source_id,
                "source_version_id": context.source_version_id,
                "module_id": context.module_id,
                "chunk_index": chunk.chunk_index,
                "token_count": chunk.token_count,
                "char_count": len(_strip_nul_bytes(chunk.text) or ""),
                "text": _strip_nul_bytes(chunk.text) or "",
                "normalized_text": _strip_nul_bytes(chunk.normalized_text) or "",
                "start_page": start_page,
                "end_page": end_page,
                "start_slide": start_slide,
                "end_slide": end_slide,
                "heading_path": _sanitize_json_value(chunk.metadata.get("heading_path")),
                "tags": _sanitize_json_value(chunk.metadata),
                "qdrant_point_id": _build_qdrant_point_id(context.source_version_id, chunk.chunk_index),
                "created_at": now,
            }
        )

    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM source_segments WHERE source_version_id = %s", (context.source_version_id,))
        cursor.execute("DELETE FROM chunks WHERE source_version_id = %s", (context.source_version_id,))
        for row in segment_rows:
            cursor.execute(
                """
                INSERT INTO source_segments (id, source_id, source_version_id, segment_type, segment_number, title, start_offset, end_offset, preview_storage_key, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, NULL, %s)
                """,
                (
                    row["id"],
                    row["source_id"],
                    row["source_version_id"],
                    row["segment_type"],
                    row["segment_number"],
                    row["title"],
                    row["created_at"],
                ),
            )
        for row in chunk_rows:
            cursor.execute(
                """
                INSERT INTO chunks (
                    id, source_id, source_version_id, module_id, chunk_index, token_count, char_count, text, normalized_text,
                    start_page, end_page, start_slide, end_slide, heading_path, tags, qdrant_point_id, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row["id"],
                    row["source_id"],
                    row["source_version_id"],
                    row["module_id"],
                    row["chunk_index"],
                    row["token_count"],
                    row["char_count"],
                    row["text"],
                    row["normalized_text"],
                    row["start_page"],
                    row["end_page"],
                    row["start_slide"],
                    row["end_slide"],
                    Json(row["heading_path"]) if row["heading_path"] is not None else None,
                    Json(row["tags"]) if row["tags"] is not None else None,
                    row["qdrant_point_id"],
                    row["created_at"],
                ),
            )
        connection.commit()
    return chunk_rows


def finalize_job_success(*, context: IngestionContext, indexed_count: int) -> list[str]:
    now = datetime.now(UTC)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT qdrant_point_id
            FROM chunks
            WHERE source_id = %s AND source_version_id <> %s
            """,
            (context.source_id, context.source_version_id),
        )
        old_point_ids = [row["qdrant_point_id"] for row in cursor.fetchall() if row.get("qdrant_point_id")]
        cursor.execute(
            """
            DELETE FROM source_segments AS segments
            WHERE segments.source_id = %s
              AND segments.source_version_id <> %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM citations
                  WHERE citations.source_segment_id = segments.id
              )
            """,
            (context.source_id, context.source_version_id),
        )
        cursor.execute(
            """
            DELETE FROM chunks AS old_chunks
            WHERE old_chunks.source_id = %s
              AND old_chunks.source_version_id <> %s
              AND NOT EXISTS (
                  SELECT 1 FROM chunk_citations WHERE chunk_citations.chunk_id = old_chunks.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM citations WHERE citations.chunk_id = old_chunks.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM quiz_item_citations WHERE quiz_item_citations.chunk_id = old_chunks.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM retrieval_trace_items WHERE retrieval_trace_items.chunk_id = old_chunks.id
              )
            """,
            (context.source_id, context.source_version_id),
        )
        cursor.execute(
            """
            UPDATE source_versions
            SET status = %s
            WHERE source_id = %s AND id <> %s AND status = %s
            """,
            (SOURCE_STATUS_ARCHIVED.lower(), context.source_id, context.source_version_id, SOURCE_STATUS_INDEXED.lower()),
        )
        cursor.execute(
            """
            UPDATE source_versions
            SET status = %s
            WHERE id = %s
            """,
            (SOURCE_STATUS_INDEXED.lower(), context.source_version_id),
        )
        cursor.execute(
            """
            UPDATE sources
            SET status = %s, version_number = %s
            WHERE id = %s
            """,
            (SOURCE_STATUS_INDEXED, context.source_version_number, context.source_id),
        )
        cursor.execute(
            """
            UPDATE ingestion_jobs
            SET status = %s, stage = 'completed', indexed_count = %s, finished_at = %s
            WHERE id = %s
            """,
            (JOB_STATUS_COMPLETED, indexed_count, now, context.job_id),
        )
        connection.commit()
    return old_point_ids


def fail_job(*, context: IngestionContext, stage: str | None, error_code: str, error_message: str) -> None:
    now = datetime.now(UTC)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM source_segments WHERE source_version_id = %s", (context.source_version_id,))
        cursor.execute("DELETE FROM chunks WHERE source_version_id = %s", (context.source_version_id,))
        cursor.execute("UPDATE source_versions SET status = %s WHERE id = %s", (SOURCE_STATUS_FAILED.lower(), context.source_version_id))
        cursor.execute(
            """
            SELECT 1
            FROM source_versions
            WHERE source_id = %s AND id <> %s AND status = %s
            LIMIT 1
            """,
            (context.source_id, context.source_version_id, SOURCE_STATUS_INDEXED.lower()),
        )
        has_prior_indexed_version = cursor.fetchone() is not None
        cursor.execute(
            "UPDATE sources SET status = %s WHERE id = %s",
            (SOURCE_STATUS_INDEXED if has_prior_indexed_version else SOURCE_STATUS_FAILED, context.source_id),
        )
        cursor.execute(
            """
            UPDATE ingestion_jobs
            SET status = %s, stage = %s, error_code = %s, error_message = %s, finished_at = %s
            WHERE id = %s
            """,
            (JOB_STATUS_FAILED, stage or "failed", error_code, error_message, now, context.job_id),
        )
        connection.commit()
