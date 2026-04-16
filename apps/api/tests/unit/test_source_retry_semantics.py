from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.enums import IngestionJobType, JobStatus, SourceStatus
from app.schemas.sources import SourceCreate
from app.services import sources as source_service_module
from app.services.sources import SourceService


pytestmark = pytest.mark.unit


class _FakeDB:
    def add(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = str(uuid4())

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        return None

    def refresh(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = str(uuid4())

    def rollback(self) -> None:
        return None


class _FakeStorage:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def head_object(self, storage_key: str):
        return SimpleNamespace(etag="etag-1", byte_size=len(self.payload))

    def get_object_bytes(self, storage_key: str) -> bytes:
        return self.payload


class _FakeSourcesRepository:
    def __init__(self, duplicate=None) -> None:
        self.duplicate = duplicate
        self.saved_intent = None
        self.intent = None
        self.latest_job = None
        self.latest_jobs_by_source: dict[str, object] = {}
        self.latest_version = None

    def get_upload_intent(self, intent_id: str):
        return self.intent

    def save_upload_intent(self, intent):
        self.saved_intent = intent
        return intent

    def get_by_checksum(self, notebook_id: str, checksum_sha256: str):
        return self.duplicate

    def get(self, source_id: str):
        return None

    def get_latest_job_for_source(self, source_id: str):
        return self.latest_jobs_by_source.get(source_id, self.latest_job)

    def list_latest_jobs_for_sources(self, source_ids: list[str]):
        return {source_id: self.latest_jobs_by_source[source_id] for source_id in source_ids if source_id in self.latest_jobs_by_source}

    def get_latest_source_version(self, source_id: str):
        return self.latest_version

    def get_job(self, job_id: str):
        return self.latest_job if getattr(self.latest_job, "id", None) == job_id else None


class _FakeNotebookRepository:
    def get_membership(self, notebook_id: str, user_id: str):
        return SimpleNamespace(notebook_id=notebook_id, user_id=user_id, role="instructor")

    def get(self, notebook_id: str):
        return SimpleNamespace(id=notebook_id, institution_id="inst-1")


class _FakeAuditService:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, **kwargs):
        self.records.append(kwargs)
        return kwargs


def _build_service(
    monkeypatch,
    *,
    duplicate=None,
    payload: bytes = b"sample pdf",
) -> tuple[SourceService, _FakeSourcesRepository, _FakeAuditService, list[dict]]:
    queue_calls: list[dict] = []
    service = SourceService.__new__(SourceService)
    service.db = _FakeDB()
    service.sources = _FakeSourcesRepository(duplicate=duplicate)
    service.notebooks = _FakeNotebookRepository()
    service.audit = _FakeAuditService()
    service.storage = _FakeStorage(payload)
    monkeypatch.setattr(source_service_module, "require_notebook_access", lambda membership, write=False: None)
    original_add = service.db.add

    def _capture_add(obj) -> None:
        original_add(obj)
        class_name = obj.__class__.__name__
        if class_name == "IngestionJob":
            service.sources.latest_job = obj
        elif class_name == "Source":
            service.sources.saved_source = obj

    service.db.add = _capture_add

    intent_id = str(uuid4())
    service.sources.intent = SimpleNamespace(
        id=intent_id,
        notebook_id="nb-1",
        created_by_user_id="user-1",
        completed_at=None,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        storage_key="storage/key.pdf",
        original_filename="report.pdf",
        sanitized_filename="report.pdf",
        mime_type="application/pdf",
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        verified_at=None,
        object_etag=None,
        error_message=None,
    )

    def _enqueue(**kwargs):
        queue_calls.append(kwargs)
        latest_job = service.sources.latest_job
        if latest_job is not None:
            latest_job.status = JobStatus.QUEUED
            latest_job.stage = "queued"
        return None

    monkeypatch.setattr(source_service_module, "enqueue_source_ingestion_job", _enqueue)
    return service, service.sources, service.audit, queue_calls


def _build_payload(intent_id: str, checksum: str, byte_size: int) -> SourceCreate:
    return SourceCreate(
        upload_intent_id=intent_id,
        source_type="pdf",
        title="Report",
        original_filename="report.pdf",
        storage_key="storage/key.pdf",
        mime_type="application/pdf",
        checksum_sha256=checksum,
        byte_size=byte_size,
        language_code="en",
    )


def test_create_source_returns_failed_source_conflict_with_existing_retry_target(monkeypatch) -> None:
    payload_bytes = b"sample pdf"
    latest_failed_job = SimpleNamespace(
        id="job-failed",
        job_type=IngestionJobType.REINDEX,
        status=JobStatus.FAILED,
        stage="parse",
        error_code="ingestion_failed",
        error_message="bad pdf",
        attempt_count=2,
        finished_at=datetime.now(UTC),
    )
    failed_duplicate = SimpleNamespace(
        id="source-old",
        status=SourceStatus.FAILED,
        deleted_at=None,
    )
    service, sources_repo, _, _ = _build_service(monkeypatch, duplicate=failed_duplicate, payload=payload_bytes)
    sources_repo.latest_job = latest_failed_job

    with pytest.raises(HTTPException) as exc_info:
        service.create_source(
            "nb-1",
            _build_payload(service.sources.intent.id, hashlib.sha256(payload_bytes).hexdigest(), len(payload_bytes)),
            SimpleNamespace(id="user-1"),
        )

    assert exc_info.value.status_code == 409
    assert sources_repo.saved_intent.error_message == "This file already exists as a failed source; retry that row instead."
    assert exc_info.value.detail["code"] == "failed_source_exists"
    assert exc_info.value.detail["details"]["existing_source_id"] == "source-old"
    assert exc_info.value.detail["details"]["latest_job"]["id"] == "job-failed"
    assert exc_info.value.detail["details"]["latest_job"]["job_type"] == IngestionJobType.REINDEX.value


def test_create_source_queues_job_and_returns_latest_job_summary(monkeypatch) -> None:
    payload_bytes = b"sample pdf"
    service, sources_repo, audit, queue_calls = _build_service(monkeypatch, payload=payload_bytes)

    created = service.create_source(
        "nb-1",
        _build_payload(service.sources.intent.id, hashlib.sha256(payload_bytes).hexdigest(), len(payload_bytes)),
        SimpleNamespace(id="user-1"),
    )

    assert created.status == SourceStatus.PROCESSING
    assert created.latest_job is not None
    assert created.latest_job.status == JobStatus.QUEUED
    assert created.latest_job.stage == "queued"
    assert queue_calls and queue_calls[0]["source_id"] == created.id
    assert queue_calls[0]["job_id"] == created.latest_job.id
    assert any(record["action_type"] == "source.create" for record in audit.records)


def test_reindex_source_reuses_same_source_identity(monkeypatch) -> None:
    queue_calls: list[dict] = []
    service = SourceService.__new__(SourceService)
    service.db = _FakeDB()
    service.sources = _FakeSourcesRepository()
    service.notebooks = _FakeNotebookRepository()
    service.audit = _FakeAuditService()
    service.storage = _FakeStorage(b"sample pdf")
    monkeypatch.setattr(source_service_module, "require_notebook_access", lambda membership, write=False: None)
    monkeypatch.setattr(source_service_module, "enqueue_source_ingestion_job", lambda **kwargs: queue_calls.append(kwargs))

    source = SimpleNamespace(
        id="source-1",
        notebook_id="nb-1",
        status=SourceStatus.FAILED,
        version_number=1,
        latest_job=None,
    )
    latest_version = SimpleNamespace(
        id="version-1",
        source_id="source-1",
        version_number=1,
        storage_key="storage/key.pdf",
        checksum_sha256=hashlib.sha256(b"sample pdf").hexdigest(),
    )
    service.sources.get = lambda source_id: source if source_id == "source-1" else None
    service.sources.get_latest_source_version = lambda source_id: latest_version if source_id == "source-1" else None
    service.sources.get_job = lambda job_id: service.sources.latest_job

    original_add = service.db.add

    def _capture_add(obj) -> None:
        original_add(obj)
        class_name = obj.__class__.__name__
        if class_name == "IngestionJob":
            service.sources.latest_job = obj

    service.db.add = _capture_add

    job = service.reindex_source("source-1", SimpleNamespace(id="user-1"))

    assert queue_calls
    assert queue_calls[0]["source_id"] == "source-1"
    assert queue_calls[0]["source_version_id"] != "version-1"
    assert job.source_id == "source-1"
    assert source.status == SourceStatus.PROCESSING
    assert job.job_type == IngestionJobType.REINDEX
    assert job.status == JobStatus.RETRYING
    assert job.stage == "retrying"
