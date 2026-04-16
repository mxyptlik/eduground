from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import DBSession, get_current_membership, get_current_user
from app.models.enums import InstitutionRole
from app.policies.rbac import require_institution_role
from app.schemas.analytics import AuditLogResponse, EvaluationResponse, NotebookAnalyticsResponse
from app.services.analytics import AnalyticsService

router = APIRouter()


@router.get("/notebooks/{notebook_id}/analytics", response_model=NotebookAnalyticsResponse)
def notebook_analytics(notebook_id: UUID, db: DBSession, user=Depends(get_current_user)) -> NotebookAnalyticsResponse:
    return NotebookAnalyticsResponse(**AnalyticsService(db).notebook_overview(str(notebook_id), user))


@router.get("/admin/audit-logs", response_model=list[AuditLogResponse])
def audit_logs(db: DBSession, user=Depends(get_current_user), membership=Depends(get_current_membership)) -> list[AuditLogResponse]:
    require_institution_role(membership, {InstitutionRole.ADMIN, InstitutionRole.INSTRUCTOR})
    return [AuditLogResponse.model_validate(item) for item in AnalyticsService(db).audit_logs()]


@router.get("/admin/evaluations", response_model=list[EvaluationResponse])
def evaluations(db: DBSession, user=Depends(get_current_user), membership=Depends(get_current_membership)) -> list[EvaluationResponse]:
    require_institution_role(membership, {InstitutionRole.ADMIN, InstitutionRole.INSTRUCTOR})
    return [EvaluationResponse.model_validate(item) for item in AnalyticsService(db).evaluations()]
