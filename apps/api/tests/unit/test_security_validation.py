from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.chat import ChatMessageCreate
from app.schemas.notes import NoteCreate
from app.schemas.sources import SourceCreate, UploadUrlRequest

pytestmark = pytest.mark.unit


def test_upload_url_rejects_invalid_checksum() -> None:
    with pytest.raises(ValidationError):
        UploadUrlRequest(
            filename="lecture-notes.pdf",
            mime_type="application/pdf",
            byte_size=1024,
            checksum_sha256="not-a-valid-checksum",
        )


def test_chat_rejects_dangerous_markup() -> None:
    with pytest.raises(ValidationError):
        ChatMessageCreate(
            content_markdown="<script>alert('xss')</script>",
            selected_source_ids=[],
            selected_module_ids=[],
        )


def test_upload_url_rejects_path_traversal_filename() -> None:
    with pytest.raises(ValidationError):
        UploadUrlRequest(
            filename="../secrets.pdf",
            mime_type="application/pdf",
            byte_size=1024,
            checksum_sha256="a" * 64,
        )


def test_source_create_rejects_invalid_storage_key() -> None:
    with pytest.raises(ValidationError):
        SourceCreate(
            upload_intent_id="5d65e07d-9456-446f-a4d5-2f495fac2eb6",
            source_type="pdf",
            title="Lecture Notes",
            original_filename="lecture.pdf",
            storage_key="../lecture.pdf",
            mime_type="application/pdf",
            checksum_sha256="b" * 64,
            byte_size=2048,
            language_code="en",
        )


def test_note_allows_markdown_but_blocks_script_tags() -> None:
    with pytest.raises(ValidationError):
        NoteCreate(
            title="Review",
            content_markdown="Safe text\n<script>bad()</script>",
        )


def test_source_create_rejects_invalid_module_uuid() -> None:
    with pytest.raises(ValidationError):
        SourceCreate(
            upload_intent_id="5d65e07d-9456-446f-a4d5-2f495fac2eb6",
            module_id="not-a-uuid",
            source_type="pdf",
            title="Lecture Notes",
            original_filename="lecture.pdf",
            storage_key="uploads/notebook-1/lecture.pdf",
            mime_type="application/pdf",
            checksum_sha256="b" * 64,
            byte_size=2048,
            language_code="en",
        )
