from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.content import Source
from app.models.enums import SourceStatus
from app.models.identity import User
from app.models.learning import Note, Quiz
from app.models.tutoring import ChatSession
from app.policies.rbac import require_notebook_access
from app.repositories.notebooks import NotebookRepository
from app.repositories.operations import OperationsRepository


class AnalyticsService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.notebooks = NotebookRepository(db)
        self.operations = OperationsRepository(db)

    def notebook_overview(self, notebook_id: str, user: User) -> dict:
        require_notebook_access(self.notebooks.get_membership(notebook_id, user.id))
        source_count = self.db.query(Source).filter(Source.notebook_id == notebook_id).count()
        indexed_source_count = (
            self.db.query(Source)
            .filter(Source.notebook_id == notebook_id, Source.status == SourceStatus.INDEXED)
            .count()
        )
        chat_session_count = self.db.query(ChatSession).filter(ChatSession.notebook_id == notebook_id).count()
        note_count = self.db.query(Note).filter(Note.notebook_id == notebook_id).count()
        quiz_count = self.db.query(Quiz).filter(Quiz.notebook_id == notebook_id).count()
        return {
            "notebook_id": notebook_id,
            "source_count": source_count,
            "indexed_source_count": indexed_source_count,
            "chat_session_count": chat_session_count,
            "note_count": note_count,
            "quiz_count": quiz_count,
        }

    def audit_logs(self):
        return self.operations.list_audit_logs()

    def evaluations(self):
        return self.operations.list_evaluations()
