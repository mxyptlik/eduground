from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.curriculum import Notebook, NotebookMembership
from app.models.content import Source


class NotebookRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, notebook: Notebook) -> Notebook:
        self.db.add(notebook)
        self.db.commit()
        self.db.refresh(notebook)
        return notebook

    def get(self, notebook_id: str) -> Notebook | None:
        return self.db.get(Notebook, notebook_id)

    def list_for_institution(self, institution_id: str) -> list[Notebook]:
        return (
            self.db.query(Notebook)
            .filter(Notebook.institution_id == institution_id)
            .order_by(Notebook.created_at.desc())
            .all()
        )

    def list_for_user(self, user_id: str) -> list[Notebook]:
        return (
            self.db.query(Notebook)
            .join(NotebookMembership, NotebookMembership.notebook_id == Notebook.id)
            .filter(NotebookMembership.user_id == user_id)
            .order_by(Notebook.created_at.desc())
            .all()
        )

    def source_count(self, notebook_id: str) -> int:
        return (
            self.db.query(Source)
            .filter(Source.notebook_id == notebook_id, Source.deleted_at.is_(None))
            .count()
        )

    def learner_count(self, notebook_id: str) -> int:
        return self.db.query(NotebookMembership).filter(NotebookMembership.notebook_id == notebook_id).count()

    def save(self, notebook: Notebook) -> Notebook:
        self.db.add(notebook)
        self.db.commit()
        self.db.refresh(notebook)
        return notebook

    def add_membership(self, membership: NotebookMembership) -> NotebookMembership:
        self.db.add(membership)
        self.db.commit()
        self.db.refresh(membership)
        return membership

    def get_membership(self, notebook_id: str, user_id: str) -> NotebookMembership | None:
        return (
            self.db.query(NotebookMembership)
            .filter(NotebookMembership.notebook_id == notebook_id, NotebookMembership.user_id == user_id)
            .first()
        )
