from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_log_context
from app.models.operations import AuditLog
from app.repositories.operations import OperationsRepository


class AuditService:
    def __init__(self, db: Session) -> None:
        self.repository = OperationsRepository(db)

    def record(
        self,
        *,
        actor_user_id: str | None,
        action_type: str,
        resource_type: str,
        resource_id: str,
        notebook_id: str | None = None,
        metadata: dict | None = None,
    ) -> AuditLog:
        log_context = get_log_context()
        metadata_json = dict(metadata or {})
        if log_context.get("request_id"):
            metadata_json.setdefault("request_id", log_context["request_id"])
        if log_context.get("path"):
            metadata_json.setdefault("path", log_context["path"])
        if log_context.get("method"):
            metadata_json.setdefault("method", log_context["method"])
        audit_log = AuditLog(
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
        return self.repository.create_audit_log(audit_log)
