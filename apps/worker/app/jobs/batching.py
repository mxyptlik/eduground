"""Queue-level ingestion batching: buffer uploads, flush as one shared run.

When ``CURRICULUM_TUTOR_INGESTION_BATCH_ENABLED`` is set, per-source ingestion
messages are buffered per course notebook in Redis and a delayed flush actor
collects them into one run that shares embedding requests across files. The
per-source job remains the sole writer (DB chunks, Qdrant points, job records)
and the correctness fallback: the batch phase only pre-warms deterministic
parse/chunk texts and their vectors. A pre-warm failure just means that file
embeds itself later; the flag off means the original path runs untouched.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import lru_cache
from threading import Event, Thread
from uuid import uuid4

import dramatiq

from app.bootstrap import configure_broker
from app.ledger import load_ledger_guard
from app.logging import get_logger
from app.services.chunking import build_chunks
from app.services.database import load_ingestion_context
from app.services.embeddings import EmbeddingBatchSession, embed_texts, embedding_batch_session
from app.services.figures import scanned_page_ratio
from app.services.normalization import normalize_document
from app.services.ocr import should_run_ocr
from app.services.parsing import parse_source_bytes
from app.services.prepared import PreparedSource, PreparedTexts, prepared_sources
from app.services.storage import get_object_bytes
from app.services.vision import build_vision_client
from app.services.visual import enrich_with_figures
from app.settings import settings
from app.telemetry import bind_context, stage_span

configure_broker()
logger = get_logger("worker.jobs.batching")
BATCH_ACTOR_NAME = "flush_ingestion_batch"
BUFFER_PREFIX = "eduground:ingestion_batch:"
CLAIM_BATCH_LUA = """
local rows = redis.call('LPOP', KEYS[1], tonumber(ARGV[1]))
if not rows then return {} end
for _,row in ipairs(rows) do redis.call('RPUSH', KEYS[2], row) end
return rows
"""
RENEW_LOCK_LUA = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('PEXPIRE', KEYS[1], ARGV[2]) else return 0 end"
RELEASE_LOCK_LUA = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end"


def batch_key_for(notebook_id: str, institution_id: str) -> str:
    return f"{institution_id}:{notebook_id}"


@lru_cache(maxsize=1)
def _redis():
    from redis import Redis

    return Redis.from_url(settings.redis_url, decode_responses=True,
                          socket_connect_timeout=2, socket_timeout=2,
                          max_connections=16)


def buffer_reference(batch_key: str, ref: dict) -> int:
    client = _redis()
    key = BUFFER_PREFIX + batch_key
    length = client.rpush(key, json.dumps(ref, sort_keys=True))
    return int(length)


def _claimed_key(batch_key: str) -> str:
    return BUFFER_PREFIX + batch_key + ":claimed"


def claimed_count(batch_key: str) -> int:
    return int(_redis().llen(_claimed_key(batch_key)))


def acknowledge(batch_key: str, ref: dict) -> None:
    _redis().lrem(_claimed_key(batch_key), 1, json.dumps(ref, sort_keys=True))


@contextmanager
def batch_lock(batch_key: str):
    client = _redis()
    key = BUFFER_PREFIX + batch_key + ":lock"
    token = uuid4().hex
    if not client.set(key, token, nx=True, px=90_000):
        yield False
        return
    stopped = Event()
    lost = Event()
    def renew():
        while not stopped.wait(30):
            try:
                if not client.eval(RENEW_LOCK_LUA, 1, key, token, 90_000):
                    lost.set()
                    return
            except Exception:
                lost.set()
                return
    thread = Thread(target=renew, daemon=True, name="ingestion-batch-lock")
    thread.start()
    try:
        yield lost
    finally:
        stopped.set()
        thread.join(timeout=5)
        try:
            client.eval(RELEASE_LOCK_LUA, 1, key, token)
        except Exception:
            pass  # Lease expires after an interrupted Redis connection.


def mark_batch_deadline(batch_key: str, *, window_ms: int) -> None:
    """Slide the quiet-period deadline forward on every buffered upload.

    The batch flushes ``window_ms`` after the *last* arrival, not the first:
    each push overwrites the deadline, and a flush that fires too early simply
    re-schedules itself for the remaining time.
    """
    client = _redis()
    key = f"{BUFFER_PREFIX}{batch_key}:deadline"
    client.set(key, int(time.time() * 1000) + max(0, int(window_ms)))
    client.expire(key, 3600)


def take_batch(batch_key: str, max_files: int) -> list[dict]:
    client = _redis()
    items = client.eval(CLAIM_BATCH_LUA, 2, BUFFER_PREFIX + batch_key,
                        _claimed_key(batch_key), max_files) or []
    return [json.loads(item) for item in items]


def queue_depth(batch_key: str) -> int:
    return int(_redis().llen(BUFFER_PREFIX + batch_key))


def schedule_flush(batch_key: str, *, delay_ms: int = 0) -> None:
    flush_ingestion_batch.send_with_options(args=(batch_key,), delay=max(0, delay_ms))


def _prepared_texts(ref: dict, *, guard, vision) -> list[str]:
    """Deterministic parse -> chunk texts for one upload; OCR files are skipped.

    Skipping OCR-requiring files avoids paying for OCR twice (pre-warm, then the
    real job); those files simply embed themselves in the per-source step.
    """
    context = load_ingestion_context(
        job_id=ref["job_id"], source_id=ref["source_id"],
        source_version_id=ref["source_version_id"], institution_id=ref["institution_id"],
    )
    filename = context.original_filename or f"{context.title}.{context.source_type}"
    raw_bytes = get_object_bytes(context.storage_key)
    parse_exc = None
    parsed = None
    try:
        parsed = parse_source_bytes(raw_bytes, filename)
    except Exception as exc:
        parse_exc = exc
        if not filename.lower().endswith(".pdf"):
            raise
    needs_ocr = (
        parse_exc is not None
        or (parsed is not None and parsed.metadata.get("requires_fallback_ocr"))
        or (parsed is not None and should_run_ocr(parsed.text, raw_bytes, threshold=0.15))
        or (parsed is not None and settings.visual_enabled
            and (scanned_page_ratio(raw_bytes, filename) or 0.0) >= 0.5)
    )
    if needs_ocr:
        raise RuntimeError("ocr-required: pre-warm skipped for this file")
    progress = {"extraction_method": parsed.metadata.get("extraction_method")}
    if settings.visual_enabled and parsed is not None:
        parsed, _summary = enrich_with_figures(
            parsed, raw_bytes, filename, settings=settings, guard=guard, vision=vision)
        progress["visual"] = _summary
    normalized = normalize_document(parsed)
    chunks = build_chunks(normalized, min_tokens=settings.chunk_min_tokens,
                          max_tokens=settings.chunk_max_tokens, overlap_tokens=settings.chunk_overlap_tokens)
    if not chunks:
        raise RuntimeError("no chunks could be prepared")
    return PreparedTexts([chunk.text for chunk in chunks], PreparedSource(normalized, chunks, progress))


@dramatiq.actor(actor_name=BATCH_ACTOR_NAME, max_retries=0)
def flush_ingestion_batch(batch_key: str) -> dict | None:
    """Flush once the quiet period after the last upload has elapsed."""
    with batch_lock(batch_key) as lock:
        if lock is False:
            schedule_flush(batch_key, delay_ms=30_000)
            return {"deferred_ms": 30_000, "reason": "another_batch_running"}
        return _flush_locked(batch_key, lock)


def _flush_locked(batch_key: str, lost_lock: Event) -> dict | None:
    client = _redis()
    deadline_key = f"{BUFFER_PREFIX}{batch_key}:deadline"
    deadline_raw = client.get(deadline_key)
    if deadline_raw is not None:
        try:
            remaining_ms = int(deadline_raw) - int(time.time() * 1000)
        except (TypeError, ValueError):
            remaining_ms = 0
        if remaining_ms > 0:
            schedule_flush(batch_key, delay_ms=remaining_ms)
            return {"deferred_ms": remaining_ms}
    if claimed_count(batch_key):
        logger.error("Interrupted ingestion batch requires review before retry",
                     extra={"extra_json": {"batch_key": batch_key, "claimed_count": claimed_count(batch_key)}})
        return {"manual_recovery_required": True, "claimed_count": claimed_count(batch_key)}
    client.delete(deadline_key)
    refs = take_batch(batch_key, max(1, settings.ingestion_batch_max_files))
    if not refs:
        return None
    # The durable claimed list remains until each source actor returns. An
    # independent delayed message exposes an interrupted claim after a crash.
    schedule_flush(batch_key, delay_ms=90_000)
    from app.jobs.ingestion import process_source_ingestion_job

    with bind_context(job_id=f"batch:{uuid4().hex[:12]}", run_id=uuid4().hex):
        guard = load_ledger_guard()
        vision = build_vision_client(guard)
        max_embed = max(1, settings.ingestion_batch_max_embed_batch)
        session = EmbeddingBatchSession()
        per_file_texts: list[list[str]] = [[] for _ in refs]
        skipped = 0
        workers = max(1, min(int(getattr(settings, "ingestion_batch_workers", 8)), len(refs)))
        preprocess_started = time.perf_counter()
        with stage_span("batch.preprocess", logger=logger,
                        attributes={"files": len(refs), "workers": workers}):
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_prepared_texts, ref, guard=guard, vision=vision): index
                           for index, ref in enumerate(refs)}
                for future, index in futures.items():
                    try:
                        per_file_texts[index] = future.result()
                    except Exception as exc:
                        skipped += 1
                        logger.warning("Batch pre-warm skipped a file",
                                       extra={"extra_json": {"source_version_id": refs[index].get("source_version_id"),
                                                             "error": f"{type(exc).__name__}: {exc}"}})
        preprocess_ms = int((time.perf_counter() - preprocess_started) * 1000)
        texts: list[str] = []
        owners: list[int] = []
        for index, file_texts in enumerate(per_file_texts):
            for text in file_texts:
                texts.append(text)
                owners.append(index)
        vectors: dict[int, list[list[float]]] = {}
        embed_failed = 0
        embed_started = time.perf_counter()
        with stage_span("batch.embed", logger=logger, attributes={"texts": len(texts), "group_size": max_embed}):
            for start in range(0, len(texts), max_embed):
                group = texts[start:start + max_embed]
                try:
                    group_vectors = embed_texts(group, guard=guard)
                    if len(group_vectors) != len(group):
                        raise RuntimeError(f"embedding count mismatch: {len(group_vectors)} != {len(group)}")
                except Exception as exc:
                    embed_failed += 1
                    logger.warning("Batch embedding group failed; those files embed per-source",
                                   extra={"extra_json": {"error": f"{type(exc).__name__}: {exc}"}})
                    continue
                for offset, vector in enumerate(group_vectors):
                    vectors.setdefault(owners[start + offset], []).append(vector)
        embed_ms = int((time.perf_counter() - embed_started) * 1000)
        for index, file_texts in enumerate(per_file_texts):
            file_vectors = vectors.get(index, [])
            if file_texts and len(file_vectors) == len(file_texts):
                session.store(file_texts, file_vectors)
        prewarmed_files = sum(1 for file_texts in per_file_texts if file_texts)
        write_started = time.perf_counter()
        with stage_span("batch.write", logger=logger, attributes={"files": len(refs)}):
            prepared = {ref["source_version_id"]: texts.prepared for ref, texts in zip(refs, per_file_texts)
                        if isinstance(texts, PreparedTexts)}
            with embedding_batch_session(session), prepared_sources(prepared):
                for index, ref in enumerate(refs):
                    if lost_lock.is_set():
                        raise RuntimeError("Ingestion batch lock was lost before source dispatch")
                    # direct=True runs the real ingestion body; without it the actor
                    # would re-buffer the file and the buffer would cycle forever.
                    process_source_ingestion_job.fn(ref["job_id"], ref["source_id"],
                                                    ref["source_version_id"], ref["institution_id"],
                                                    direct=True)
                    acknowledge(batch_key, ref)
                    session.forget(per_file_texts[index])
                    per_file_texts[index] = []
        write_ms = int((time.perf_counter() - write_started) * 1000)
        remaining = queue_depth(batch_key)
        if remaining > 0:
            schedule_flush(batch_key)
        summary = {"files": len(refs), "prewarmed_files": prewarmed_files,
                   "skipped": skipped, "embed_failed_groups": embed_failed, "remaining": remaining,
                   "workers": workers, "preprocess_ms": preprocess_ms, "embed_ms": embed_ms,
                   "write_ms": write_ms}
        logger.info("Completed batched ingestion run", extra={"extra_json": summary})
        return summary
