from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ORMModel


class NotebookAnalyticsResponse(BaseModel):
    notebook_id: str
    source_count: int
    indexed_source_count: int
    chat_session_count: int
    note_count: int
    quiz_count: int


class AuditLogResponse(ORMModel):
    id: str
    action_type: str
    resource_type: str
    resource_id: str
    notebook_id: str | None
    metadata_json: dict


class EvaluationResponse(ORMModel):
    id: str
    notebook_id: str | None
    model_profile_id: str
    evaluation_type: str
    status: str

