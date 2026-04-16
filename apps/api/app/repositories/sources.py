from __future__ import annotations

from sqlalchemy import desc
from sqlalchemy.orm import aliased
from sqlalchemy.orm import Session

from app.models.content import Chunk, IngestionJob, Source, SourceSegment, SourceUploadIntent, SourceVersion


class SourceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, source: Source) -> Source:
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)
        return source

    def save(self, source: Source) -> Source:
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)
        return source

    def get(self, source_id: str) -> Source | None:
        return self.db.get(Source, source_id)

    def get_by_checksum(self, notebook_id: str, checksum_sha256: str) -> Source | None:
        return (
            self.db.query(Source)
            .filter(Source.notebook_id == notebook_id)
            .filter(Source.checksum_sha256 == checksum_sha256)
            .filter(Source.deleted_at.is_(None))
            .order_by(desc(Source.created_at))
            .first()
        )

    def get_by_storage_key(self, storage_key: str) -> Source | None:
        return self.db.query(Source).filter(Source.storage_key == storage_key).first()

    def list_for_notebook(self, notebook_id: str) -> list[Source]:
        return (
            self.db.query(Source)
            .filter(Source.notebook_id == notebook_id)
            .filter(Source.deleted_at.is_(None))
            .order_by(Source.created_at.desc())
            .all()
        )

    def create_upload_intent(self, intent: SourceUploadIntent) -> SourceUploadIntent:
        self.db.add(intent)
        self.db.commit()
        self.db.refresh(intent)
        return intent

    def save_upload_intent(self, intent: SourceUploadIntent) -> SourceUploadIntent:
        self.db.add(intent)
        self.db.commit()
        self.db.refresh(intent)
        return intent

    def get_upload_intent(self, intent_id: str) -> SourceUploadIntent | None:
        return self.db.get(SourceUploadIntent, intent_id)

    def create_source_version(self, version: SourceVersion) -> SourceVersion:
        self.db.add(version)
        self.db.commit()
        self.db.refresh(version)
        return version

    def save_source_version(self, version: SourceVersion) -> SourceVersion:
        self.db.add(version)
        self.db.commit()
        self.db.refresh(version)
        return version

    def get_source_version(self, source_version_id: str) -> SourceVersion | None:
        return self.db.get(SourceVersion, source_version_id)

    def get_latest_source_version(self, source_id: str) -> SourceVersion | None:
        return (
            self.db.query(SourceVersion)
            .filter(SourceVersion.source_id == source_id)
            .order_by(SourceVersion.version_number.desc())
            .first()
        )

    def list_source_versions(self, source_id: str) -> list[SourceVersion]:
        return (
            self.db.query(SourceVersion)
            .filter(SourceVersion.source_id == source_id)
            .order_by(SourceVersion.version_number.desc())
            .all()
        )

    def replace_segments(self, source_version_id: str, segments: list[SourceSegment]) -> list[SourceSegment]:
        self.db.query(SourceSegment).filter(SourceSegment.source_version_id == source_version_id).delete()
        self.db.add_all(segments)
        self.db.commit()
        return segments

    def replace_chunks(self, source_version_id: str, chunks: list[Chunk]) -> list[Chunk]:
        self.db.query(Chunk).filter(Chunk.source_version_id == source_version_id).delete()
        self.db.add_all(chunks)
        self.db.commit()
        return chunks

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        return self.db.get(Chunk, chunk_id)

    def list_chunks_for_source(self, source_id: str) -> list[Chunk]:
        return (
            self.db.query(Chunk)
            .filter(Chunk.source_id == source_id)
            .order_by(Chunk.chunk_index.asc())
            .all()
        )

    def list_chunks_for_version(self, source_version_id: str) -> list[Chunk]:
        return (
            self.db.query(Chunk)
            .filter(Chunk.source_version_id == source_version_id)
            .order_by(Chunk.chunk_index.asc())
            .all()
        )

    def create_job(self, job: IngestionJob) -> IngestionJob:
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def save_job(self, job: IngestionJob) -> IngestionJob:
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def get_job(self, job_id: str) -> IngestionJob | None:
        return self.db.get(IngestionJob, job_id)

    def list_jobs_for_source(self, source_id: str) -> list[IngestionJob]:
        return (
            self.db.query(IngestionJob)
            .filter(IngestionJob.source_id == source_id)
            .order_by(IngestionJob.created_at.desc())
            .all()
        )

    def get_latest_job_for_source(self, source_id: str) -> IngestionJob | None:
        return (
            self.db.query(IngestionJob)
            .filter(IngestionJob.source_id == source_id)
            .order_by(IngestionJob.created_at.desc())
            .first()
        )

    def list_latest_jobs_for_sources(self, source_ids: list[str]) -> dict[str, IngestionJob]:
        if not source_ids:
            return {}
        latest_job = aliased(IngestionJob)
        latest_timestamp_rows = (
            self.db.query(
                IngestionJob.source_id.label("source_id"),
                IngestionJob.created_at.label("created_at"),
            )
            .filter(IngestionJob.source_id.in_(source_ids))
            .order_by(IngestionJob.source_id.asc(), IngestionJob.created_at.desc())
            .all()
        )
        latest_created_at_by_source: dict[str, object] = {}
        for row in latest_timestamp_rows:
            latest_created_at_by_source.setdefault(row.source_id, row.created_at)
        if not latest_created_at_by_source:
            return {}
        jobs = (
            self.db.query(latest_job)
            .filter(latest_job.source_id.in_(list(latest_created_at_by_source.keys())))
            .order_by(latest_job.created_at.desc())
            .all()
        )
        results: dict[str, IngestionJob] = {}
        for job in jobs:
            expected_created_at = latest_created_at_by_source.get(job.source_id)
            if expected_created_at is not None and job.created_at == expected_created_at and job.source_id not in results:
                results[job.source_id] = job
        return results
