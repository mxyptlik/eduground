from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import IngestionJobType, JobStatus, SourceStatus, SourceType
from app.schemas.common import ORMModel
from app.schemas.validators import (
    normalize_text,
    validate_checksum_sha256,
    validate_filename,
    validate_language_code,
    validate_mime_type,
    validate_storage_key,
    validate_uuid_string,
)


class UploadUrlRequest(BaseModel):
    filename: str
    mime_type: str
    byte_size: int = Field(gt=0)
    checksum_sha256: str

    @field_validator("filename")
    @classmethod
    def validate_filename_value(cls, value: str) -> str:
        return validate_filename(value)

    @field_validator("mime_type")
    @classmethod
    def validate_mime_type_value(cls, value: str) -> str:
        return validate_mime_type(value)

    @field_validator("checksum_sha256")
    @classmethod
    def validate_checksum_value(cls, value: str) -> str:
        return validate_checksum_sha256(value)


class UploadUrlResponse(BaseModel):
    upload_intent_id: str
    storage_key: str
    upload_url: str
    expires_in_seconds: int


class SourceCreate(BaseModel):
    upload_intent_id: str
    module_id: str | None = None
    source_type: SourceType
    title: str
    original_filename: str | None = None
    storage_key: str
    mime_type: str
    checksum_sha256: str
    byte_size: int = Field(gt=0)
    language_code: str | None = None

    @field_validator("upload_intent_id", "module_id")
    @classmethod
    def validate_optional_uuid_fields(cls, value: str | None, info) -> str | None:
        return validate_uuid_string(value, field_name=info.field_name)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return normalize_text(value, field_name="title", max_length=255)

    @field_validator("original_filename")
    @classmethod
    def validate_original_filename(cls, value: str | None) -> str | None:
        return None if value is None else validate_filename(value, field_name="original_filename")

    @field_validator("storage_key")
    @classmethod
    def validate_storage_key_value(cls, value: str) -> str:
        return validate_storage_key(value)

    @field_validator("mime_type")
    @classmethod
    def validate_mime_type_value(cls, value: str) -> str:
        return validate_mime_type(value)

    @field_validator("checksum_sha256")
    @classmethod
    def validate_checksum_value(cls, value: str) -> str:
        return validate_checksum_sha256(value)

    @field_validator("language_code")
    @classmethod
    def validate_language_code_value(cls, value: str | None) -> str | None:
        return validate_language_code(value)


class SourceResponse(ORMModel):
    id: str
    notebook_id: str
    module_id: str | None
    source_type: SourceType
    title: str
    storage_key: str
    mime_type: str
    checksum_sha256: str
    byte_size: int
    status: SourceStatus
    version_number: int
    latest_job: "IngestionJobSummaryResponse | None" = None


class SourceReindexRequest(BaseModel):
    force: bool = False


class IngestionJobResponse(ORMModel):
    id: str
    source_id: str
    source_version_id: str | None
    job_type: IngestionJobType
    status: JobStatus
    attempt_count: int
    stage: str | None
    error_code: str | None
    error_message: str | None
    progress_json: dict | None
    segment_count: int
    chunk_count: int
    embedded_count: int
    indexed_count: int
    started_at: datetime | None
    finished_at: datetime | None
    trace_id: str | None


class IngestionJobSummaryResponse(ORMModel):
    id: str
    job_type: IngestionJobType
    status: JobStatus
    stage: str | None
    error_code: str | None
    error_message: str | None
    attempt_count: int
    finished_at: datetime | None
