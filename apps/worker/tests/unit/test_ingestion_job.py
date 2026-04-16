from __future__ import annotations

from inspect import signature
from types import SimpleNamespace

import dramatiq

from app.jobs import ingestion as ingestion_jobs

def test_process_source_ingestion_job_accepts_ids_only() -> None:
    parameters = list(signature(ingestion_jobs.process_source_ingestion_job.fn).parameters)

    assert parameters == ["job_id", "source_id", "source_version_id", "institution_id"]


def test_process_source_ingestion_job_is_registered_under_expected_actor_name() -> None:
    actor = dramatiq.get_broker().get_actor(ingestion_jobs.INGESTION_ACTOR_NAME)

    assert actor is ingestion_jobs.process_source_ingestion_job


def test_process_source_ingestion_job_runs_worker_owned_pipeline(monkeypatch) -> None:
    stage_updates: list[str] = []
    upserted_points: list[dict] = []
    deleted_points: list[str] = []

    context = SimpleNamespace(
        job_id="job-1",
        source_id="source-1",
        source_version_id="version-1",
        institution_id="inst-1",
        storage_key="storage/key.docx",
        original_filename="report.docx",
        title="Report",
        source_type="docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        notebook_id="nb-1",
        module_id=None,
    )
    parsed = SimpleNamespace(
        text="Alpha",
        segments=[
            SimpleNamespace(
                segment_type="content",
                segment_number=1,
                title=None,
                text="Alpha",
                metadata={"structure_kind": "paragraph", "heading_path": []},
            )
        ],
        metadata={"extraction_method": "pdfplumber", "requires_fallback_ocr": False},
    )
    chunk = SimpleNamespace(
        chunk_index=1,
        text="Alpha",
        normalized_text="alpha",
        token_count=1,
        start_segment=1,
        end_segment=1,
        metadata={"dedup_hash": "hash"},
    )
    persisted_chunk = {
        "id": "chunk-1",
        "chunk_index": 1,
        "text": "Alpha",
        "qdrant_point_id": "version-1:1",
        "tags": {"dedup_hash": "hash"},
        "start_page": 1,
        "end_page": 1,
        "start_slide": None,
        "end_slide": None,
    }

    monkeypatch.setattr(ingestion_jobs, "load_ingestion_context", lambda **kwargs: context)
    monkeypatch.setattr(ingestion_jobs, "mark_job_running", lambda **kwargs: None)
    monkeypatch.setattr(ingestion_jobs, "update_job_stage", lambda **kwargs: stage_updates.append(kwargs["stage"]))
    monkeypatch.setattr(ingestion_jobs, "get_object_bytes", lambda storage_key: b"raw-bytes")
    monkeypatch.setattr(ingestion_jobs, "parse_source_bytes", lambda raw_bytes, filename: parsed)
    monkeypatch.setattr(ingestion_jobs, "should_run_ocr", lambda text, raw_bytes, threshold: False)
    monkeypatch.setattr(ingestion_jobs, "normalize_document", lambda document: document)
    monkeypatch.setattr(ingestion_jobs, "build_chunks", lambda *args, **kwargs: [chunk])
    monkeypatch.setattr(ingestion_jobs, "replace_segments_and_chunks", lambda **kwargs: [persisted_chunk])
    monkeypatch.setattr(ingestion_jobs, "embed_texts", lambda texts: [[0.1, 0.2]])
    monkeypatch.setattr(ingestion_jobs, "ensure_collection", lambda vector_size: None)
    monkeypatch.setattr(ingestion_jobs, "upsert_points", lambda points: upserted_points.extend(points))
    monkeypatch.setattr(ingestion_jobs, "finalize_job_success", lambda **kwargs: ["old-point-1"])
    monkeypatch.setattr(ingestion_jobs, "delete_points", lambda point_ids: deleted_points.extend(point_ids))

    result = ingestion_jobs.process_source_ingestion_job.fn("job-1", "source-1", "version-1", "inst-1")

    assert result is None
    assert stage_updates == ["parse", "normalize", "chunk", "embed", "embed", "index", "cleanup"]
    assert upserted_points[0]["payload"]["chunk_id"] == "chunk-1"
    assert upserted_points[0]["payload"]["source_id"] == "source-1"
    assert deleted_points == ["old-point-1"]


def test_process_source_ingestion_job_marks_failure(monkeypatch) -> None:
    fail_calls: list[dict] = []

    context = SimpleNamespace(
        job_id="job-1",
        source_id="source-1",
        source_version_id="version-1",
        institution_id="inst-1",
        storage_key="storage/key.docx",
        original_filename="report.docx",
        title="Report",
        source_type="docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        notebook_id="nb-1",
        module_id=None,
    )

    monkeypatch.setattr(ingestion_jobs, "load_ingestion_context", lambda **kwargs: context)
    monkeypatch.setattr(ingestion_jobs, "mark_job_running", lambda **kwargs: None)
    monkeypatch.setattr(ingestion_jobs, "update_job_stage", lambda **kwargs: None)
    monkeypatch.setattr(ingestion_jobs, "get_object_bytes", lambda storage_key: b"raw-bytes")
    monkeypatch.setattr(ingestion_jobs, "parse_source_bytes", lambda raw_bytes, filename: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ingestion_jobs, "fail_job", lambda **kwargs: fail_calls.append(kwargs))
    monkeypatch.setattr(ingestion_jobs, "delete_points", lambda point_ids: None)

    result = ingestion_jobs.process_source_ingestion_job.fn("job-1", "source-1", "version-1", "inst-1")

    assert result is None
    assert fail_calls
    assert fail_calls[0]["stage"] == "parse"
