from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.operations import AuditLog, Evaluation


class OperationsRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_audit_log(self, audit_log: AuditLog) -> AuditLog:
        self.db.add(audit_log)
        self.db.commit()
        self.db.refresh(audit_log)
        return audit_log

    def list_audit_logs(self) -> list[AuditLog]:
        return self.db.query(AuditLog).order_by(AuditLog.created_at.desc()).all()

    def list_evaluations(self) -> list[Evaluation]:
        return self.db.query(Evaluation).order_by(Evaluation.started_at.desc()).all()

