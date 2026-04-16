from __future__ import annotations

from fastapi import HTTPException, status

from app.models.curriculum import NotebookMembership
from app.models.enums import InstitutionRole, NotebookMembershipRole
from app.models.identity import InstitutionMembership, User

WRITE_ROLES = {
    NotebookMembershipRole.OWNER,
    NotebookMembershipRole.EDITOR,
    NotebookMembershipRole.INSTRUCTOR,
    NotebookMembershipRole.TA,
}
READ_ROLES = WRITE_ROLES | {NotebookMembershipRole.VIEWER, NotebookMembershipRole.STUDENT}
ADMIN_ROLES = {InstitutionRole.ADMIN, InstitutionRole.INSTRUCTOR}


def require_institution_role(membership: InstitutionMembership | None, allowed: set[InstitutionRole]) -> None:
    if membership is None or membership.role not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Institution role is not allowed")


def require_notebook_access(
    notebook_membership: NotebookMembership | None,
    *,
    write: bool = False,
) -> None:
    if notebook_membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Notebook access denied")
    allowed = WRITE_ROLES if write else READ_ROLES
    if notebook_membership.role not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Notebook access denied")


def can_manage_members(user: User, membership: NotebookMembership | None) -> bool:
    return bool(membership and membership.role in {NotebookMembershipRole.OWNER, NotebookMembershipRole.INSTRUCTOR})

