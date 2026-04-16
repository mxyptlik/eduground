from __future__ import annotations

from uuid import uuid4

from app.logging import get_logger
from app.services.chunking import build_chunks
from app.services.embeddings import embed_texts
from app.services.indexing import build_qdrant_payload
from app.services.indexing import build_qdrant_point
from app.services.normalization import normalize_document
from app.services.ocr import run_ocr, should_run_ocr
from app.services.parsing import parse_source_bytes
from app.services.vectorstore import ensure_collection, upsert_points
from app.telemetry import bind_context, stage_span

logger = get_logger("worker.ingestion")


def run_ingestion_pipeline(
    raw_bytes: bytes,
    filename: str,
    *,
    notebook_id: str,
    institution_id: str,
    source_id: str,
    source_version_id: str,
    mime_type: str = "application/octet-stream",
    job_id: str | None = None,
    run_id: str | None = None,
) -> dict:
    effective_job_id = job_id or f"ingest:{source_version_id}"
    effective_run_id = run_id or uuid4().hex
    common_attributes = {
        "filename": filename,
        "notebook_id": notebook_id,
        "institution_id": institution_id,
        "source_id": source_id,
        "source_version_id": source_version_id,
    }
    with bind_context(job_id=effective_job_id, run_id=effective_run_id):
        logger.info("Starting worker ingestion pipeline", extra={"extra_json": common_attributes})
        with stage_span("pipeline.parse", logger=logger, attributes=common_attributes):
            try:
                parsed = parse_source_bytes(raw_bytes, filename)
                parse_exc = None
            except Exception as exc:
                parse_exc = exc
                if not filename.lower().endswith(".pdf"):
                    raise
                parsed = None
        if parse_exc is not None:
            with stage_span("pipeline.ocr", logger=logger, attributes=common_attributes):
                ocr_text = run_ocr(raw_bytes, filename=filename, mime_type=mime_type)
                parsed = parse_source_bytes(ocr_text.encode("utf-8"), f"{filename}.ocr.txt")
        elif parsed.metadata.get("requires_fallback_ocr") or should_run_ocr(parsed.text, raw_bytes, threshold=0.15):
            with stage_span("pipeline.ocr", logger=logger, attributes=common_attributes):
                ocr_text = run_ocr(raw_bytes, filename=filename, mime_type=mime_type)
                parsed = parse_source_bytes(ocr_text.encode("utf-8"), f"{filename}.ocr.txt")
        with stage_span("pipeline.normalize", logger=logger, attributes=common_attributes):
            normalized = normalize_document(parsed)
        with stage_span("pipeline.chunk", logger=logger, attributes=common_attributes):
            chunks = build_chunks(normalized)
            if not chunks:
                raise RuntimeError(f"No chunks generated for {filename}")
            payloads = [
                build_qdrant_payload(
                    chunk,
                    notebook_id=notebook_id,
                    institution_id=institution_id,
                    source_id=source_id,
                    source_version_id=source_version_id,
                )
                for chunk in chunks
            ]
        with stage_span("pipeline.embed", logger=logger, attributes={**common_attributes, "chunk_count": len(chunks)}):
            vectors = embed_texts([chunk.text for chunk in chunks])
            if len(vectors) != len(chunks):
                raise RuntimeError(f"Embedding count mismatch: expected {len(chunks)}, got {len(vectors)}")
        with stage_span("pipeline.index", logger=logger, attributes={**common_attributes, "chunk_count": len(chunks)}):
            ensure_collection(len(vectors[0]))
            points = [
                build_qdrant_point(
                    chunk,
                    vectors[index],
                    notebook_id=notebook_id,
                    institution_id=institution_id,
                    source_id=source_id,
                    source_version_id=source_version_id,
                )
                for index, chunk in enumerate(chunks)
            ]
            upsert_points(points)
        logger.info(
            "Completed worker ingestion pipeline",
            extra={"extra_json": {**common_attributes, "indexed_chunk_count": len(points)}},
        )
        return {
            "document": normalized,
            "chunks": chunks,
            "payloads": payloads,
            "indexed_chunk_count": len(points),
            "point_ids": [point["id"] for point in points],
        }
