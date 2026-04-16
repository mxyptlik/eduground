from __future__ import annotations

from uuid import uuid4

import dramatiq

from app.bootstrap import configure_broker
from app.services.chunking import build_chunks
from app.services.database import (
    fail_job,
    finalize_job_success,
    load_ingestion_context,
    mark_job_running,
    replace_segments_and_chunks,
    update_job_stage,
)
from app.services.embeddings import embed_texts
from app.logging import configure_logging, get_logger
from app.services.normalization import normalize_document
from app.services.ocr import OCRProviderError, run_ocr, should_run_ocr
from app.services.parsing import parse_source_bytes
from app.services.storage import get_object_bytes
from app.services.vectorstore import delete_points, ensure_collection, upsert_points
from app.settings import settings
from app.telemetry import bind_context, configure_telemetry, stage_span

configure_logging("worker")
configure_telemetry("curriculum-tutor-worker")
configure_broker()
logger = get_logger("worker.jobs.ingestion")
INGESTION_ACTOR_NAME = "process_source_ingestion_job"


@dramatiq.actor(actor_name=INGESTION_ACTOR_NAME, max_retries=0)
def process_source_ingestion_job(
    job_id: str,
    source_id: str,
    source_version_id: str,
    institution_id: str,
) -> dict:
    run_id = uuid4().hex
    with bind_context(job_id=job_id, run_id=run_id):
        with stage_span(
            "job.process_source_ingestion",
            logger=logger,
            attributes={"source_id": source_id, "source_version_id": source_version_id, "institution_id": institution_id},
        ):
            logger.info(
                "Received process_source_ingestion_job",
                extra={"extra_json": {"job_id": job_id, "source_id": source_id, "source_version_id": source_version_id}},
            )
            context = load_ingestion_context(
                job_id=job_id,
                source_id=source_id,
                source_version_id=source_version_id,
                institution_id=institution_id,
            )
            filename = context.original_filename or f"{context.title}.{context.source_type}"
            current_stage = "download"
            staged_point_ids: list[str] = []
            try:
                mark_job_running(job_id=job_id)
                with stage_span("pipeline.download", logger=logger, attributes={"filename": filename, "job_id": job_id}):
                    raw_bytes = get_object_bytes(context.storage_key)

                current_stage = "parse"
                update_job_stage(job_id=job_id, stage=current_stage)
                parse_exc: Exception | None = None
                parsed = None
                with stage_span("pipeline.parse", logger=logger, attributes={"filename": filename, "job_id": job_id}):
                    try:
                        parsed = parse_source_bytes(raw_bytes, filename)
                    except Exception as exc:
                        parse_exc = exc
                        if not filename.lower().endswith(".pdf"):
                            raise
                if parse_exc is not None:
                    current_stage = "ocr"
                    progress_json = {"parse_error": str(parse_exc)}
                    update_job_stage(job_id=job_id, stage=current_stage, progress_json=progress_json)
                    with stage_span("pipeline.ocr", logger=logger, attributes={"filename": filename, "job_id": job_id}):
                        ocr_text = run_ocr(raw_bytes, filename=filename, mime_type=context.mime_type)
                        parsed = parse_source_bytes(ocr_text.encode("utf-8"), f"{filename}.ocr.txt")
                    progress_json = {
                        "ocr_applied": True,
                        "ocr_provider": "unstructured",
                        "ocr_trigger": "parse_failed",
                        "parse_error": str(parse_exc),
                        "extraction_method": parsed.metadata.get("extraction_method"),
                    }
                else:
                    assert parsed is not None
                    progress_json = {"extraction_method": parsed.metadata.get("extraction_method")}
                    if parsed.metadata.get("requires_fallback_ocr") or should_run_ocr(
                        parsed.text,
                        raw_bytes,
                        threshold=settings.ocr_density_threshold,
                    ):
                        current_stage = "ocr"
                        update_job_stage(job_id=job_id, stage=current_stage, progress_json=progress_json)
                        with stage_span("pipeline.ocr", logger=logger, attributes={"filename": filename, "job_id": job_id}):
                            ocr_text = run_ocr(raw_bytes, filename=filename, mime_type=context.mime_type)
                            parsed = parse_source_bytes(ocr_text.encode("utf-8"), f"{filename}.ocr.txt")
                        progress_json = {
                            **progress_json,
                            "ocr_applied": True,
                            "ocr_provider": "unstructured",
                            "ocr_trigger": "empty_document",
                            "extraction_method": parsed.metadata.get("extraction_method"),
                        }

                current_stage = "normalize"
                update_job_stage(
                    job_id=job_id,
                    stage=current_stage,
                    progress_json=progress_json,
                    segment_count=len(parsed.segments),
                )
                with stage_span("pipeline.normalize", logger=logger, attributes={"job_id": job_id}):
                    normalized = normalize_document(parsed)

                current_stage = "chunk"
                with stage_span("pipeline.chunk", logger=logger, attributes={"job_id": job_id}):
                    chunks = build_chunks(
                        normalized,
                        min_tokens=settings.chunk_min_tokens,
                        max_tokens=settings.chunk_max_tokens,
                        overlap_tokens=settings.chunk_overlap_tokens,
                    )
                if not chunks:
                    raise RuntimeError("No searchable content could be extracted from the uploaded source.")
                update_job_stage(
                    job_id=job_id,
                    stage=current_stage,
                    segment_count=len(normalized.segments),
                    chunk_count=len(chunks),
                    progress_json=progress_json,
                )

                current_stage = "persist"
                with stage_span("pipeline.persist", logger=logger, attributes={"job_id": job_id, "chunk_count": len(chunks)}):
                    chunk_rows = replace_segments_and_chunks(context=context, document=normalized, chunks=chunks)

                current_stage = "embed"
                update_job_stage(job_id=job_id, stage=current_stage, chunk_count=len(chunk_rows), progress_json=progress_json)
                with stage_span("pipeline.embed", logger=logger, attributes={"job_id": job_id, "chunk_count": len(chunk_rows)}):
                    vectors = embed_texts([row["text"] for row in chunk_rows])
                if len(vectors) != len(chunk_rows):
                    raise RuntimeError(f"Embedding count mismatch: expected {len(chunk_rows)}, got {len(vectors)}")
                update_job_stage(
                    job_id=job_id,
                    stage=current_stage,
                    embedded_count=len(vectors),
                    chunk_count=len(chunk_rows),
                    progress_json=progress_json,
                )

                current_stage = "index"
                with stage_span("pipeline.index", logger=logger, attributes={"job_id": job_id, "chunk_count": len(chunk_rows)}):
                    ensure_collection(len(vectors[0]))
                    points = []
                    for index, row in enumerate(chunk_rows):
                        payload = {
                            "chunk_id": row["id"],
                            "source_id": context.source_id,
                            "source_version_id": context.source_version_id,
                            "notebook_id": context.notebook_id,
                            "institution_id": context.institution_id,
                            "module_id": context.module_id,
                            "status": "indexed",
                            "chunk_index": row["chunk_index"],
                            "dedup_hash": (row["tags"] or {}).get("dedup_hash"),
                            "start_page": row["start_page"],
                            "end_page": row["end_page"],
                            "start_slide": row["start_slide"],
                            "end_slide": row["end_slide"],
                        }
                        points.append({"id": row["qdrant_point_id"], "vector": vectors[index], "payload": payload})
                    upsert_points(points)
                    staged_point_ids = [point["id"] for point in points]
                update_job_stage(
                    job_id=job_id,
                    stage=current_stage,
                    embedded_count=len(vectors),
                    indexed_count=len(staged_point_ids),
                    chunk_count=len(chunk_rows),
                    progress_json=progress_json,
                )

                current_stage = "cleanup"
                update_job_stage(job_id=job_id, stage=current_stage, indexed_count=len(staged_point_ids), progress_json=progress_json)
                old_point_ids = finalize_job_success(context=context, indexed_count=len(staged_point_ids))
                if old_point_ids:
                    delete_points(old_point_ids)
                logger.info(
                    "Completed process_source_ingestion_job",
                    extra={"extra_json": {"job_id": job_id, "source_id": source_id, "indexed_count": len(staged_point_ids)}},
                )
                return None
            except Exception as exc:
                logger.exception(
                    "process_source_ingestion_job failed",
                    extra={"extra_json": {"job_id": job_id, "source_id": source_id, "stage": current_stage}},
                )
                error_code = "ocr_failed" if isinstance(exc, OCRProviderError) else "ingestion_failed"
                fail_job(
                    context=context,
                    stage=current_stage,
                    error_code=error_code,
                    error_message=str(exc),
                )
                if staged_point_ids:
                    try:
                        delete_points(staged_point_ids)
                    except Exception:
                        logger.exception(
                            "Failed to clean up staged qdrant points after ingestion failure",
                            extra={"extra_json": {"job_id": job_id, "staged_point_count": len(staged_point_ids)}},
                        )
                return None
