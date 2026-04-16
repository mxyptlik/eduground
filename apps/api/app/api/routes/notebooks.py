from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import DBSession, get_current_user
from app.schemas.notebooks import NotebookCreate, NotebookListItemResponse, NotebookMembershipCreate, NotebookPolicyUpdate, NotebookResponse, NotebookUpdate
from app.services.notebooks import NotebookService

router = APIRouter(prefix="/notebooks")


@router.get("", response_model=list[NotebookListItemResponse])
def list_notebooks(db: DBSession, user=Depends(get_current_user)) -> list[NotebookListItemResponse]:
    return [NotebookListItemResponse.model_validate(item) for item in NotebookService(db).list_for_user(user)]


@router.post("", response_model=NotebookResponse)
def create_notebook(payload: NotebookCreate, db: DBSession, user=Depends(get_current_user)) -> NotebookResponse:
    return NotebookResponse.model_validate(NotebookService(db).create(payload, user))


@router.get("/{notebook_id}", response_model=NotebookResponse)
def get_notebook(notebook_id: UUID, db: DBSession, user=Depends(get_current_user)) -> NotebookResponse:
    return NotebookResponse.model_validate(NotebookService(db).get(str(notebook_id), user))


@router.patch("/{notebook_id}", response_model=NotebookResponse)
def update_notebook(notebook_id: UUID, payload: NotebookUpdate, db: DBSession, user=Depends(get_current_user)) -> NotebookResponse:
    return NotebookResponse.model_validate(NotebookService(db).update(str(notebook_id), payload, user))


@router.post("/{notebook_id}/members")
def add_notebook_member(notebook_id: UUID, payload: NotebookMembershipCreate, db: DBSession, user=Depends(get_current_user)) -> dict:
    membership = NotebookService(db).add_member(str(notebook_id), payload, user)
    return {"id": membership.id, "notebook_id": membership.notebook_id, "user_id": membership.user_id, "role": membership.role}


@router.patch("/{notebook_id}/policy-mode", response_model=NotebookResponse)
def update_policy_mode(notebook_id: UUID, payload: NotebookPolicyUpdate, db: DBSession, user=Depends(get_current_user)) -> NotebookResponse:
    return NotebookResponse.model_validate(NotebookService(db).update_policy(str(notebook_id), payload, user))
