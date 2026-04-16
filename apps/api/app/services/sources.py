from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.queue import enqueue_source_ingestion_job
from app.integrations.storage.factory import get_object_storage
from app.models.content import IngestionJob, Source, SourceUploadIntent, SourceVersion
from app.models.enums import IngestionJobType, JobStatus, SourceStatus
from app.models.identity import User
from app.policies.rbac import require_notebook_access
from app.repositories.notebooks import NotebookRepository
from app.repositories.sources import SourceRepository
from app.schemas.sources import IngestionJobSummaryResponse, SourceCreate, UploadUrlRequest, UploadUrlResponse
from app.services.audit import AuditService


class SourceService:
    ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md"}

    def __init__(self, db: Session) -> None:
        self.db = db
        self.sources = SourceRepository(db)
        self.notebooks = NotebookRepository(db)
        self.audit = AuditService(db)
        self.storage = get_object_storage()

    def create_upload_url(self, notebook_id: str, payload: UploadUrlRequest, user: User) -> UploadUrlResponse:
        require_notebook_access(self.notebooks.get_membership(notebook_id, user.id), write=True)
        suffix = Path(payload.filename).suffix.lower()
        if suffix not in self.ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported file type")
        if payload.byte_size > settings.max_upload_bytes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File exceeds size limit")

        sanitized_filename = self._sanitize_filename(payload.filename)
        upload_intent_id = str(uuid4())
        storage_key = (
            f"notebooks/{notebook_id}/uploads/{payload.checksum_sha256}/"
            f"{upload_intent_id}_{sanitized_filename}"
        )
        intent = SourceUploadIntent(
            id=upload_intent_id,
            notebook_id=notebook_id,
            created_by_user_id=user.id,
            original_filename=payload.filename,
            sanitized_filename=sanitized_filename,
            mime_type=payload.mime_type,
            checksum_sha256=payload.checksum_sha256,
            byte_size=payload.byte_size,
            storage_key=storage_key,
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.signed_url_ttl_seconds),
            created_at=datetime.now(UTC),
        )
        intent = self.sources.create_upload_intent(intent)
        return UploadUrlResponse(
            upload_intent_id=intent.id,
            storage_key=storage_key,
            upload_url=self.storage.create_signed_upload_url(storage_key, payload.mime_type),
            expires_in_seconds=settings.signed_url_ttl_seconds,
        )

    def create_source(self, notebook_id: str, payload: SourceCreate, user: User) -> Source:
        membership = self.notebooks.get_membership(notebook_id, user.id)
        require_notebook_access(membership, write=True)
        notebook = self.notebooks.get(notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        intent = self.sources.get_upload_intent(payload.upload_intent_id)
        if intent is None or intent.notebook_id != notebook_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload intent not found")
        if intent.created_by_user_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Upload intent does not belong to the current user")
        if intent.completed_at is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload intent has already been finalized")
        if intent.expires_at < datetime.now(UTC):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload intent has expired")
        if payload.storage_key != intent.storage_key:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload storage key does not match the signed upload intent")

        object_metadata = self.storage.head_object(intent.storage_key)
        if object_metadata is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded object was not found in storage")

        raw_bytes = self.storage.get_object_bytes(intent.storage_key)
        computed_hash = hashlib.sha256(raw_bytes).hexdigest()
        if computed_hash != intent.checksum_sha256:
            intent.error_message = "Uploaded object checksum does not match the signed upload intent"
            self.sources.save_upload_intent(intent)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=intent.error_message)
        if len(raw_bytes) != intent.byte_size:
            intent.error_message = "Uploaded object size does not match the signed upload intent"
            self.sources.save_upload_intent(intent)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=intent.error_message)

        duplicate = self.sources.get_by_checksum(notebook_id, intent.checksum_sha256)
        if duplicate is not None and duplicate.deleted_at is None:
            if duplicate.status == SourceStatus.FAILED:
                latest_job = self.sources.get_latest_job_for_source(duplicate.id)
                intent.error_message = "This file already exists as a failed source; retry that row instead."
                self.sources.save_upload_intent(intent)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "failed_source_exists",
                        "message": intent.error_message,
                        "retryable": False,
                        "details": {
                            "existing_source_id": duplicate.id,
                            "latest_job": self._serialize_latest_job(latest_job),
                        },
                    },
                )
            intent.error_message = "A source with the same file checksum already exists in this notebook"
            self.sources.save_upload_intent(intent)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "conflict",
                    "message": intent.error_message,
                    "retryable": False,
                    "details": {"existing_source_id": duplicate.id},
                },
            )

        source = Source(
            notebook_id=notebook_id,
            module_id=payload.module_id,
            source_type=payload.source_type,
            title=payload.title,
            original_filename=intent.original_filename,
            storage_key=intent.storage_key,
            mime_type=intent.mime_type,
            checksum_sha256=intent.checksum_sha256,
            byte_size=intent.byte_size,
            language_code=payload.language_code,
            status=SourceStatus.PROCESSING,
            created_by_user_id=user.id,
        )
        try:
            self.db.add(source)
            self.db.flush()
            source_version = SourceVersion(
                source_id=source.id,
                version_number=1,
                storage_key=intent.storage_key,
                checksum_sha256=intent.checksum_sha256,
                status=SourceStatus.PROCESSING.value,
                created_at=datetime.now(UTC),
                created_by_user_id=user.id,
            )
            self.db.add(source_version)
            self.db.flush()
            job = IngestionJob(
                source_id=source.id,
                source_version_id=source_version.id,
                job_type=IngestionJobType.PARSE,
                status=JobStatus.QUEUED,
                attempt_count=0,
                stage="queued",
                storage_key_snapshot=intent.storage_key,
                progress_json={"upload_intent_id": intent.id},
                created_at=datetime.now(UTC),
            )
            self.db.add(job)
            intent.verified_at = datetime.now(UTC)
            intent.completed_at = datetime.now(UTC)
            intent.object_etag = object_metadata.etag
            intent.error_message = None
            self.db.add(intent)
            self.db.commit()
            self.db.refresh(source)
            self.db.refresh(source_version)
            self.db.refresh(job)
        except Exception as exc:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to finalize uploaded source: {exc}") from exc

        source.latest_job = job
        self.audit.record(
            actor_user_id=user.id,
            action_type="source.create",
            resource_type="source",
            resource_id=source.id,
            notebook_id=notebook_id,
            metadata={"source_version_id": source_version.id},
        )
        try:
            enqueue_source_ingestion_job(
                job_id=job.id,
                source_id=source.id,
                source_version_id=source_version.id,
                institution_id=notebook.institution_id,
            )
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.stage = "queue_failed"
            job.error_code = "queue_failed"
            job.error_message = str(exc)
            job.finished_at = datetime.now(UTC)
            source.status = SourceStatus.FAILED
            source_version.status = SourceStatus.FAILED.value
            self.db.add(job)
            self.db.add(source)
            self.db.add(source_version)
            self.db.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "ingestion_queue_unavailable",
                    "message": f"Source upload finalized, but queue dispatch failed: {exc}",
                    "retryable": True,
                },
            ) from exc
        return self._hydrate_source(source)

    def list_sources(self, notebook_id: str, user: User) -> list[Source]:
        require_notebook_access(self.notebooks.get_membership(notebook_id, user.id))
        sources = self.sources.list_for_notebook(notebook_id)
        return self._hydrate_sources(sources)

    def list_jobs(self, source_id: str, user: User) -> list[IngestionJob]:
        source = self.get_source(source_id, user)
        return self.sources.list_jobs_for_source(source.id)

    def get_source(self, source_id: str, user: User) -> Source:
        source = self.sources.get(source_id)
        if source is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
        require_notebook_access(self.notebooks.get_membership(source.notebook_id, user.id))
        return self._hydrate_source(source)

    def delete_source(self, source_id: str, user: User) -> Source:
        source = self.get_source(source_id, user)
        require_notebook_access(self.notebooks.get_membership(source.notebook_id, user.id), write=True)
        source.status = SourceStatus.DELETED
        source.deleted_at = datetime.now(UTC)
        source = self.sources.save(source)
        self.audit.record(actor_user_id=user.id, action_type="source.delete", resource_type="source", resource_id=source.id, notebook_id=source.notebook_id)
        return self._hydrate_source(source)

    def reindex_source(self, source_id: str, user: User) -> IngestionJob:
        source = self.get_source(source_id, user)
        require_notebook_access(self.notebooks.get_membership(source.notebook_id, user.id), write=True)
        notebook = self.notebooks.get(source.notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")
        latest_version = self.sources.get_latest_source_version(source.id)
        if latest_version is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Source has no source version to reindex")
        previous_status = source.status

        next_version = SourceVersion(
            source_id=source.id,
            version_number=latest_version.version_number + 1,
            storage_key=latest_version.storage_key,
            checksum_sha256=latest_version.checksum_sha256,
            status=SourceStatus.PROCESSING.value,
            created_at=datetime.now(UTC),
            created_by_user_id=user.id,
        )
        self.db.add(next_version)
        self.db.flush()
        job = IngestionJob(
            source_id=source.id,
            source_version_id=next_version.id,
            job_type=IngestionJobType.REINDEX,
            status=JobStatus.RETRYING,
            attempt_count=0,
            stage="retrying",
            storage_key_snapshot=next_version.storage_key,
            created_at=datetime.now(UTC),
        )
        self.db.add(job)
        source.status = SourceStatus.PROCESSING
        self.db.add(source)
        self.db.commit()
        self.db.refresh(next_version)
        self.db.refresh(job)
        self.db.refresh(source)
        source.latest_job = job
        self.audit.record(
            actor_user_id=user.id,
            action_type="source.reindex",
            resource_type="ingestion_job",
            resource_id=job.id,
            notebook_id=source.notebook_id,
            metadata={"source_version_id": next_version.id},
        )
        try:
            enqueue_source_ingestion_job(
                job_id=job.id,
                source_id=source.id,
                source_version_id=next_version.id,
                institution_id=notebook.institution_id,
            )
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.stage = "queue_failed"
            job.error_code = "queue_failed"
            job.error_message = str(exc)
            job.finished_at = datetime.now(UTC)
            source.status = previous_status
            next_version.status = SourceStatus.FAILED.value
            self.db.add(job)
            self.db.add(source)
            self.db.add(next_version)
            self.db.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "ingestion_queue_unavailable",
                    "message": f"Reindex queued metadata was created, but queue dispatch failed: {exc}",
                    "retryable": True,
                },
            ) from exc
        return self.sources.get_job(job.id) or job

    def _hydrate_source(self, source: Source) -> Source:
        latest_job = self.sources.get_latest_job_for_source(source.id)
        source.latest_job = latest_job
        return source

    def _hydrate_sources(self, sources: list[Source]) -> list[Source]:
        latest_jobs = self.sources.list_latest_jobs_for_sources([source.id for source in sources])
        for source in sources:
            source.latest_job = latest_jobs.get(source.id)
        return sources

    def _serialize_latest_job(self, job: IngestionJob | None) -> dict | None:
        if job is None:
            return None
        return IngestionJobSummaryResponse.model_validate(job).model_dump(mode="json")

    def _sanitize_filename(self, filename: str) -> str:
        name, extension = Path(filename).stem, Path(filename).suffix.lower()
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "upload"
        return f"{cleaned}{extension}"
