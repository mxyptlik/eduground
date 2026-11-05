from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from app.ledger import load_ledger_guard
from app.logging import get_logger
from app.services.chunking import build_chunks
from app.services.embeddings import embed_texts
from app.services.indexing import build_qdrant_payload
from app.services.indexing import build_qdrant_point
from app.services.normalization import normalize_document
from app.services.ocr import run_ocr, should_run_ocr
from app.services.parsing import parse_source_bytes
from app.services.figures import scanned_page_ratio
from app.services.vectorstore import ensure_collection, upsert_points
from app.services.vision import build_vision_client
from app.services.visual import enrich_with_figures
from app.settings import settings
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
        ledger_guard = load_ledger_guard()
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
                ocr_text = run_ocr(raw_bytes, filename=filename, mime_type=mime_type, guard=ledger_guard)
                parsed = parse_source_bytes(ocr_text.encode("utf-8"), f"{filename}.ocr.txt")
        elif (
            parsed.metadata.get("requires_fallback_ocr")
            or should_run_ocr(parsed.text, raw_bytes, threshold=0.15)
            or (settings.visual_enabled and (scanned_page_ratio(raw_bytes, filename) or 0.0) >= 0.5)
        ):
            with stage_span("pipeline.ocr", logger=logger, attributes=common_attributes):
                ocr_text = run_ocr(raw_bytes, filename=filename, mime_type=mime_type, guard=ledger_guard)
                parsed = parse_source_bytes(ocr_text.encode("utf-8"), f"{filename}.ocr.txt")
        if settings.visual_enabled:
            parsed, _visual_summary = enrich_with_figures(
                parsed, raw_bytes, filename, settings=settings,
                guard=ledger_guard, vision=build_vision_client(ledger_guard),
            )
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
            vectors = embed_texts([chunk.text for chunk in chunks], guard=ledger_guard)
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


@dataclass(slots=True)
class BatchIngestionInput:
    raw_bytes: bytes
    filename: str
    notebook_id: str
    institution_id: str
    source_id: str
    source_version_id: str
    mime_type: str = "application/octet-stream"


@dataclass(slots=True)
class BatchIngestionResult:
    source_id: str
    source_version_id: str
    filename: str
    status: str = "failed"  # indexed | failed
    error: str | None = None
    document: NormalizedDocument | None = None
    chunks: list = field(default_factory=list)
    payloads: list = field(default_factory=list)
    point_ids: list = field(default_factory=list)


def _prepare_for_batch(item: BatchIngestionInput, *, guard, vision) -> tuple:
    """Parse -> OCR fallback -> visual cards -> normalize -> chunk for one input."""
    try:
        parsed = parse_source_bytes(item.raw_bytes, item.filename)
        parse_exc = None
    except Exception as exc:
        parse_exc = exc
        if not item.filename.lower().endswith(".pdf"):
            raise
        parsed = None
    if parse_exc is not None:
        ocr_text = run_ocr(item.raw_bytes, filename=item.filename, mime_type=item.mime_type, guard=guard)
        parsed = parse_source_bytes(ocr_text.encode("utf-8"), f"{item.filename}.ocr.txt")
    elif (
        parsed.metadata.get("requires_fallback_ocr")
        or should_run_ocr(parsed.text, item.raw_bytes, threshold=0.15)
        or (settings.visual_enabled and (scanned_page_ratio(item.raw_bytes, item.filename) or 0.0) >= 0.5)
    ):
        ocr_text = run_ocr(item.raw_bytes, filename=item.filename, mime_type=item.mime_type, guard=guard)
        parsed = parse_source_bytes(ocr_text.encode("utf-8"), f"{item.filename}.ocr.txt")
    if settings.visual_enabled:
        parsed, _visual_summary = enrich_with_figures(
            parsed, item.raw_bytes, item.filename, settings=settings, guard=guard, vision=vision)
    normalized = normalize_document(parsed)
    chunks = build_chunks(normalized)
    if not chunks:
        raise RuntimeError(f"No chunks generated for {item.filename}")
    payloads = [
        build_qdrant_payload(
            chunk,
            notebook_id=item.notebook_id,
            institution_id=item.institution_id,
            source_id=item.source_id,
            source_version_id=item.source_version_id,
        )
        for chunk in chunks
    ]
    return normalized, chunks, payloads


def run_ingestion_batch(
    inputs,
    *,
    job_id: str | None = None,
    run_id: str | None = None,
    max_embed_batch: int = 64,
) -> list[BatchIngestionResult]:
    """Ingest several documents in one run, sharing embedding requests.

    Per-file parse/OCR/chunk failures are isolated. Embedding calls batch
    across files in bounded groups (so the ledger reservation stays bounded),
    while indexing remains per file. The dramatiq job entrypoint still receives
    one source per message; this is the batch building block for callers that
    hold several files at once.
    """
    items = list(inputs)
    if isinstance(max_embed_batch, bool) or not 1 <= max_embed_batch <= 512:
        raise ValueError("max_embed_batch must be between 1 and 512")
    if not items:
        return []
    results = [BatchIngestionResult(item.source_id, item.source_version_id, item.filename) for item in items]
    with bind_context(job_id=job_id or f"ingest-batch:{uuid4().hex[:12]}", run_id=run_id or uuid4().hex):
        guard = load_ledger_guard()
        vision = build_vision_client(guard)
        prepared: list[BatchIngestionResult] = []
        prepared_items: list[BatchIngestionInput] = []
        for result, item in zip(results, items):
            try:
                normalized, chunks, payloads = _prepare_for_batch(item, guard=guard, vision=vision)
            except Exception as exc:
                result.error = f"{type(exc).__name__}: {exc}"
                continue
            result.document, result.chunks, result.payloads = normalized, chunks, payloads
            prepared.append(result)
            prepared_items.append(item)

        texts: list[str] = []
        owners: list[int] = []
        for index, result in enumerate(prepared):
            for chunk in result.chunks:
                texts.append(chunk.text)
                owners.append(index)

        vectors: dict[int, list[list[float]]] = {}
        for start in range(0, len(texts), max_embed_batch):
            group = texts[start:start + max_embed_batch]
            try:
                group_vectors = embed_texts(group, guard=guard)
                if len(group_vectors) != len(group):
                    raise RuntimeError(f"Embedding count mismatch: expected {len(group)}, got {len(group_vectors)}")
            except Exception as exc:
                for index in sorted({owners[position] for position in range(start, start + len(group))}):
                    prepared[index].error = f"{type(exc).__name__}: {exc}"
                continue
            for offset, vector in enumerate(group_vectors):
                vectors.setdefault(owners[start + offset], []).append(vector)

        for index, result in enumerate(prepared):
            file_vectors = vectors.get(index, [])
            if result.error:
                continue
            if len(file_vectors) != len(result.chunks):
                result.error = "embedding incomplete for this file"
                continue
            try:
                ensure_collection(len(file_vectors[0]))
                points = [
                    build_qdrant_point(
                        chunk,
                        file_vectors[position],
                        notebook_id=prepared_items[index].notebook_id,
                        institution_id=prepared_items[index].institution_id,
                        source_id=result.source_id,
                        source_version_id=result.source_version_id,
                    )
                    for position, chunk in enumerate(result.chunks)
                ]
                upsert_points(points)
                result.point_ids = [point["id"] for point in points]
                result.status = "indexed"
            except Exception as exc:
                result.error = f"{type(exc).__name__}: {exc}"
    return results
