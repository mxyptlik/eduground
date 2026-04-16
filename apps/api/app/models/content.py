from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import IngestionJobType, JobStatus, SegmentType, SourceOrigin, SourceStatus, SourceType
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sources"

    notebook_id: Mapped[str] = mapped_column(ForeignKey("notebooks.id"), index=True)
    module_id: Mapped[str | None] = mapped_column(ForeignKey("curriculum_modules.id"), nullable=True, index=True)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType))
    title: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(255))
    checksum_sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(Integer)
    language_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[SourceStatus] = mapped_column(Enum(SourceStatus), default=SourceStatus.UPLOADED, index=True)
    is_authoritative: Mapped[bool] = mapped_column(Boolean, default=True)
    source_origin: Mapped[SourceOrigin] = mapped_column(Enum(SourceOrigin), default=SourceOrigin.UPLOAD)
    ingestion_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceUploadIntent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "source_upload_intents"

    notebook_id: Mapped[str] = mapped_column(ForeignKey("notebooks.id"), index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    sanitized_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(255))
    checksum_sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    object_etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class SourceVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "source_versions"
    __table_args__ = (UniqueConstraint("source_id", "version_number", name="uq_source_version"),)

    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(512))
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class SourceSegment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "source_segments"

    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    source_version_id: Mapped[str | None] = mapped_column(ForeignKey("source_versions.id"), nullable=True, index=True)
    segment_type: Mapped[SegmentType] = mapped_column(Enum(SegmentType))
    segment_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preview_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IngestionJob(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ingestion_jobs"

    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    source_version_id: Mapped[str | None] = mapped_column(ForeignKey("source_versions.id"), nullable=True, index=True)
    job_type: Mapped[IngestionJobType] = mapped_column(Enum(IngestionJobType), index=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.QUEUED, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key_snapshot: Mapped[str | None] = mapped_column(String(512), nullable=True)
    progress_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    segment_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedded_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Chunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "chunks"

    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), index=True)
    module_id: Mapped[str | None] = mapped_column(ForeignKey("curriculum_modules.id"), nullable=True, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)
    char_count: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    start_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_slide: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_slide: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heading_path: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    qdrant_point_id: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChunkCitation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "chunk_citations"

    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"), index=True)
    source_segment_id: Mapped[str | None] = mapped_column(ForeignKey("source_segments.id"), nullable=True)
    start_char_offset: Mapped[int] = mapped_column(Integer)
    end_char_offset: Mapped[int] = mapped_column(Integer)
    quote_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
