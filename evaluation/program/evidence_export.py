"""Export source-bound, read-only evidence packets from indexed course notebooks."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from evaluation.program.artifacts import _atomic_create


class EvidenceExportError(ValueError):
    pass


def export_course_evidence(
    session: Any, *, course_id: str, notebook_id: str,
    expected_sha256: set[str] | None = None,
) -> dict[str, Any]:
    """Capture current source versions and complete chunks without mutating DB.

    An expected checksum set is required to claim corpus membership matched to
    local originals. The current ingestion schema does not persist the actual
    embedding model or complete chunker settings per source; those fields stay
    explicitly unverified rather than inferred from current environment vars.
    """
    from app.models.content import Chunk, IngestionJob, Source, SourceVersion
    from app.models.curriculum import Notebook
    from app.models.enums import SourceOrigin, SourceStatus, SourceType

    if not course_id.strip() or not notebook_id.strip():
        raise EvidenceExportError("course_id and notebook_id are required")
    if expected_sha256 is not None:
        expected_sha256 = {digest.lower() for digest in expected_sha256}
        if not expected_sha256 or any(len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)
                                      for digest in expected_sha256):
            raise EvidenceExportError("Expected SHA-256 set must contain valid digests")
    notebook = session.get(Notebook, notebook_id)
    if notebook is None or notebook.archived_at is not None:
        raise EvidenceExportError("Notebook is missing or archived")
    sources = session.query(Source).filter(Source.notebook_id == notebook_id).order_by(Source.id).all()
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    observed: set[str] = set()
    for source in sources:
        reason = None
        if source.deleted_at is not None or source.status != SourceStatus.INDEXED:
            reason = "not_currently_indexed"
        elif source.source_type == SourceType.DERIVED_NOTE or source.source_origin == SourceOrigin.NOTE_CONVERSION:
            reason = "derived_note_excluded"
        elif expected_sha256 is not None and source.checksum_sha256.lower() not in expected_sha256:
            reason = "not_in_expected_corpus"
        if reason:
            excluded.append({"source_id": source.id, "reason": reason})
            continue
        if source.checksum_sha256.lower() in observed:
            raise EvidenceExportError(f"Duplicate indexed checksum in notebook {notebook_id}")
        versions = session.query(SourceVersion).filter(
            SourceVersion.source_id == source.id,
            SourceVersion.version_number == source.version_number,
        ).all()
        if (len(versions) != 1 or versions[0].checksum_sha256.lower() != source.checksum_sha256.lower()
                or versions[0].status != SourceStatus.INDEXED.value):
            raise EvidenceExportError(f"Source {source.id} has an inconsistent current version")
        version = versions[0]
        chunks = session.query(Chunk).filter(Chunk.source_version_id == version.id).order_by(Chunk.chunk_index).all()
        if not chunks or any(chunk.source_id != source.id or not chunk.text.strip() for chunk in chunks):
            raise EvidenceExportError(f"Source {source.id} has missing or empty current chunks")
        indices = [chunk.chunk_index for chunk in chunks]
        if len(indices) != len(set(indices)):
            raise EvidenceExportError(f"Source {source.id} has duplicate chunk indices")
        jobs = session.query(IngestionJob).filter(IngestionJob.source_id == source.id).order_by(IngestionJob.created_at).all()
        included.append({
            "source_id": source.id, "source_label": source.title,
            "original_filename": source.original_filename,
            "source_type": source.source_type.value,
            "checksum_sha256": source.checksum_sha256.lower(),
            "byte_size": source.byte_size,
            "version_number": source.version_number,
            "source_version_id": version.id,
            "ingestion_profile_id": source.ingestion_profile_id,
            "jobs": [{"job_id": job.id, "job_type": job.job_type.value, "status": job.status.value,
                      "attempt_count": job.attempt_count, "stage": job.stage, "error_code": job.error_code,
                      "progress": job.progress_json}
                     for job in jobs],
            "chunks": [{"chunk_id": chunk.id, "chunk_index": chunk.chunk_index,
                        "text": chunk.text, "token_count": chunk.token_count,
                        "start_page": chunk.start_page, "end_page": chunk.end_page,
                        "start_slide": chunk.start_slide, "end_slide": chunk.end_slide,
                        "heading_path": chunk.heading_path, "tags": chunk.tags}
                       for chunk in chunks],
        })
        observed.add(source.checksum_sha256.lower())
    if not included:
        raise EvidenceExportError("No eligible indexed source was found")
    missing = sorted(expected_sha256 - observed) if expected_sha256 is not None else []
    if missing:
        raise EvidenceExportError(f"Expected corpus files are not indexed: {len(missing)} checksum(s)")
    return {
        "schema_version": 1,
        "course_id": course_id,
        "notebook_id": notebook_id,
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "corpus_membership": "matched_to_expected_hashes" if expected_sha256 is not None else "not_verified",
        "provenance": {
            "notebook_embedding_profile_id": notebook.embedding_profile_id,
            "actual_ingestion_embedding_model": None,
            "actual_chunker_configuration": None,
            "limitation": "The current ingestion records do not persist these per-source identities; do not claim a fully reproducible index snapshot.",
        },
        "sources": included,
        "excluded_sources": excluded,
    }


def write_evidence_packet(packet: dict[str, Any], destination: Path) -> str:
    """Write JSON create-only and return a hash of the exact UTF-8 bytes."""
    body = (json.dumps(packet, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    digest = sha256(body).hexdigest()
    _atomic_create(destination, body)
    return digest
