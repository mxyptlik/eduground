from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_log_context
from app.models.content import Chunk, Source, SourceVersion
from app.models.enums import SourceOrigin, SourceStatus, SourceType
from app.models.identity import User
from app.models.learning import Note
from app.models.operations import AuditLog
from app.policies.rbac import require_notebook_access
from app.repositories.learning import LearningRepository
from app.repositories.notebooks import NotebookRepository
from app.repositories.sources import SourceRepository
from app.schemas.notes import NoteCreate, NoteUpdate
from app.services.audit import AuditService


class NoteService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.learning = LearningRepository(db)
        self.notebooks = NotebookRepository(db)
        self.sources = SourceRepository(db)
        self.audit = AuditService(db)

    def _can_author_manage_note(self, note: Note, user: User) -> bool:
        return note.author_user_id == user.id

    def _stage_audit(
        self,
        *,
        actor_user_id: str | None,
        action_type: str,
        resource_type: str,
        resource_id: str,
        notebook_id: str | None,
        metadata: dict | None = None,
    ) -> None:
        log_context = get_log_context()
        metadata_json = dict(metadata or {})
        if log_context.get("request_id"):
          metadata_json.setdefault("request_id", log_context["request_id"])
        if log_context.get("path"):
          metadata_json.setdefault("path", log_context["path"])
        if log_context.get("method"):
          metadata_json.setdefault("method", log_context["method"])
        self.db.add(
            AuditLog(
                actor_user_id=actor_user_id,
                action_type=action_type,
                resource_type=resource_type,
                resource_id=resource_id,
                notebook_id=notebook_id,
                metadata_json=metadata_json,
                created_at=datetime.now(UTC),
                ip_address=log_context.get("client_ip"),
                user_agent=log_context.get("user_agent"),
            )
        )

    def create(self, notebook_id: str, payload: NoteCreate, user: User) -> Note:
        require_notebook_access(self.notebooks.get_membership(notebook_id, user.id))
        note = Note(
            notebook_id=notebook_id,
            author_user_id=user.id,
            title=payload.title,
            content_markdown=payload.content_markdown,
            note_type=payload.note_type,
            source_chat_message_id=payload.source_chat_message_id,
            visibility=payload.visibility,
        )
        self.db.add(note)
        self.db.flush()
        self._stage_audit(
            actor_user_id=user.id,
            action_type="note.create",
            resource_type="note",
            resource_id=note.id,
            notebook_id=notebook_id,
        )
        self.db.commit()
        self.db.refresh(note)
        return note

    def list_for_notebook(self, notebook_id: str, user: User) -> list[Note]:
        require_notebook_access(self.notebooks.get_membership(notebook_id, user.id))
        return self.learning.list_notes(notebook_id)

    def update(self, note_id: str, payload: NoteUpdate, user: User) -> Note:
        note = self.learning.get_note(note_id)
        if note is None:
            raise ValueError("Note not found")
        membership = self.notebooks.get_membership(note.notebook_id, user.id)
        if self._can_author_manage_note(note, user):
            require_notebook_access(membership)
        else:
            require_notebook_access(membership, write=True)
        for field in ("title", "content_markdown", "visibility"):
            value = getattr(payload, field)
            if value is not None:
                setattr(note, field, value)
        self.db.add(note)
        self._stage_audit(
            actor_user_id=user.id,
            action_type="note.update",
            resource_type="note",
            resource_id=note.id,
            notebook_id=note.notebook_id,
        )
        self.db.commit()
        self.db.refresh(note)
        return note

    def delete(self, note_id: str, user: User) -> None:
        note = self.learning.get_note(note_id)
        if note is None:
            raise ValueError("Note not found")
        membership = self.notebooks.get_membership(note.notebook_id, user.id)
        if self._can_author_manage_note(note, user):
            require_notebook_access(membership)
        else:
            require_notebook_access(membership, write=True)
        resource_id = note.id
        notebook_id = note.notebook_id
        converted_source_id = note.converted_source_id
        self.learning.delete_note(resource_id)
        self.audit.record(
            actor_user_id=user.id,
            action_type="note.delete",
            resource_type="note",
            resource_id=resource_id,
            notebook_id=notebook_id,
            metadata={"converted_source_id": converted_source_id},
        )

    def export(self, note_id: str, user: User) -> str:
        note = self.learning.get_note(note_id)
        if note is None:
            raise ValueError("Note not found")
        require_notebook_access(self.notebooks.get_membership(note.notebook_id, user.id))
        return f"# {note.title}\n\n{note.content_markdown}\n"

    def convert_to_source(self, note_id: str, user: User) -> Source:
        note = self.learning.get_note(note_id)
        if note is None:
            raise ValueError("Note not found")
        membership = self.notebooks.get_membership(note.notebook_id, user.id)
        if self._can_author_manage_note(note, user):
            require_notebook_access(membership)
        else:
            require_notebook_access(membership, write=True)
        storage_key = f"derived/notes/{note.id}.md"
        source = Source(
            notebook_id=note.notebook_id,
            source_type=SourceType.DERIVED_NOTE,
            title=note.title,
            original_filename=f"{note.title.lower().replace(' ', '-')}.md",
            storage_key=storage_key,
            mime_type="text/markdown",
            checksum_sha256=f"note-{note.id}",
            byte_size=len(note.content_markdown.encode('utf-8')),
            language_code="en",
            status=SourceStatus.INDEXED,
            source_origin=SourceOrigin.NOTE_CONVERSION,
            created_by_user_id=user.id,
        )
        source = self.sources.create(source)
        source_version = SourceVersion(
            source_id=source.id,
            version_number=1,
            storage_key=storage_key,
            checksum_sha256=source.checksum_sha256,
            status="indexed",
            created_at=datetime.now(UTC),
            created_by_user_id=user.id,
        )
        self.db.add(source_version)
        self.db.flush()
        self.db.add(
            Chunk(
                source_id=source.id,
                source_version_id=source_version.id,
                module_id=None,
                chunk_index=1,
                token_count=max(len(note.content_markdown.split()), 1),
                char_count=len(note.content_markdown),
                text=note.content_markdown,
                normalized_text=note.content_markdown.lower(),
                heading_path=[note.title],
                tags={"origin": "note_conversion"},
                qdrant_point_id=f"derived-{source.id}-1",
                created_at=datetime.now(UTC),
            )
        )
        note.converted_source_id = source.id
        self.db.add(note)
        self.db.commit()
        self.audit.record(actor_user_id=user.id, action_type="note.convert_to_source", resource_type="source", resource_id=source.id, notebook_id=note.notebook_id, metadata={"note_id": note.id})
        return source
