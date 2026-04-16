from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.curriculum import Notebook, NotebookMembership
from app.models.enums import NotebookMembershipRole
from app.models.identity import User
from app.policies.rbac import can_manage_members, require_notebook_access
from app.repositories.notebooks import NotebookRepository
from app.schemas.notebooks import NotebookCreate, NotebookMembershipCreate, NotebookPolicyUpdate, NotebookUpdate
from app.services.audit import AuditService


class NotebookService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = NotebookRepository(db)
        self.audit = AuditService(db)

    def create(self, payload: NotebookCreate, user: User) -> Notebook:
        institution_id = user.institution_id
        if not institution_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Select or join an organization before creating a notebook",
            )
        notebook = Notebook(
            institution_id=institution_id,
            course_offering_id=payload.course_offering_id,
            title=payload.title,
            description=payload.description,
            owner_user_id=user.id,
            visibility=payload.visibility,
            policy_mode=payload.policy_mode,
        )
        notebook = self.repository.create(notebook)
        membership = NotebookMembership(
            notebook_id=notebook.id,
            user_id=user.id,
            role=NotebookMembershipRole.OWNER,
            granted_by_user_id=user.id,
            created_at=datetime.now(UTC),
        )
        self.repository.add_membership(membership)
        self.audit.record(actor_user_id=user.id, action_type="notebook.create", resource_type="notebook", resource_id=notebook.id, notebook_id=notebook.id)
        return notebook

    def list_for_user(self, user: User) -> list[dict]:
        notebooks = self.repository.list_for_user(user.id)
        return [
            {
                "id": notebook.id,
                "institution_id": notebook.institution_id,
                "title": notebook.title,
                "description": notebook.description,
                "owner_user_id": notebook.owner_user_id,
                "visibility": notebook.visibility,
                "policy_mode": notebook.policy_mode,
                "course_offering_id": notebook.course_offering_id,
                "source_count": self.repository.source_count(notebook.id),
                "learner_count": self.repository.learner_count(notebook.id),
            }
            for notebook in notebooks
        ]

    def get(self, notebook_id: str, user: User) -> Notebook:
        notebook = self.repository.get(notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")
        require_notebook_access(self.repository.get_membership(notebook_id, user.id))
        return notebook

    def update(self, notebook_id: str, payload: NotebookUpdate, user: User) -> Notebook:
        notebook = self.get(notebook_id, user)
        require_notebook_access(self.repository.get_membership(notebook_id, user.id), write=True)
        for field in ("title", "description", "visibility"):
            value = getattr(payload, field)
            if value is not None:
                setattr(notebook, field, value)
        notebook = self.repository.save(notebook)
        self.audit.record(actor_user_id=user.id, action_type="notebook.update", resource_type="notebook", resource_id=notebook.id, notebook_id=notebook.id)
        return notebook

    def add_member(self, notebook_id: str, payload: NotebookMembershipCreate, user: User) -> NotebookMembership:
        manager_membership = self.repository.get_membership(notebook_id, user.id)
        if not can_manage_members(user, manager_membership):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot manage notebook members")
        membership = NotebookMembership(
            notebook_id=notebook_id,
            user_id=payload.user_id,
            role=payload.role,
            granted_by_user_id=user.id,
            created_at=datetime.now(UTC),
        )
        membership = self.repository.add_membership(membership)
        self.audit.record(actor_user_id=user.id, action_type="notebook.member.add", resource_type="notebook_membership", resource_id=membership.id, notebook_id=notebook_id, metadata={"member_user_id": payload.user_id, "role": payload.role})
        return membership

    def update_policy(self, notebook_id: str, payload: NotebookPolicyUpdate, user: User) -> Notebook:
        notebook = self.get(notebook_id, user)
        require_notebook_access(self.repository.get_membership(notebook_id, user.id), write=True)
        notebook.policy_mode = payload.policy_mode
        notebook = self.repository.save(notebook)
        self.audit.record(actor_user_id=user.id, action_type="notebook.policy.update", resource_type="notebook", resource_id=notebook.id, notebook_id=notebook.id, metadata={"policy_mode": payload.policy_mode})
        return notebook
