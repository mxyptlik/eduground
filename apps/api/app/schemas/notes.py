from __future__ import annotations

from pydantic import BaseModel, field_validator

from app.models.enums import NoteType, NoteVisibility
from app.schemas.common import ORMModel
from app.schemas.validators import normalize_optional_text, normalize_text, validate_uuid_string


class NoteCreate(BaseModel):
    title: str
    content_markdown: str
    note_type: NoteType = NoteType.MANUAL
    source_chat_message_id: str | None = None
    visibility: NoteVisibility = NoteVisibility.PRIVATE

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return normalize_text(value, field_name="title", max_length=255)

    @field_validator("content_markdown")
    @classmethod
    def validate_content(cls, value: str) -> str:
        return normalize_text(
            value,
            field_name="content_markdown",
            max_length=20_000,
            allow_newlines=True,
            allow_dangerous_markup=False,
        )

    @field_validator("source_chat_message_id")
    @classmethod
    def validate_source_chat_message_id(cls, value: str | None) -> str | None:
        return validate_uuid_string(value, field_name="source_chat_message_id")


class NoteUpdate(BaseModel):
    title: str | None = None
    content_markdown: str | None = None
    visibility: NoteVisibility | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return normalize_optional_text(value, field_name="title", max_length=255)

    @field_validator("content_markdown")
    @classmethod
    def validate_content(cls, value: str | None) -> str | None:
        return normalize_optional_text(
            value,
            field_name="content_markdown",
            max_length=20_000,
            allow_newlines=True,
            allow_dangerous_markup=False,
        )


class NoteResponse(ORMModel):
    id: str
    notebook_id: str
    author_user_id: str
    title: str
    content_markdown: str
    note_type: NoteType
    converted_source_id: str | None
    visibility: NoteVisibility


class NoteExportResponse(BaseModel):
    note_id: str
    export_format: str
    content: str
