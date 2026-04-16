from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import DeleteStrategy, EvaluationStatus, EvaluationType, ModelOperationType, ModelProfileStatus
from app.models.mixins import UUIDPrimaryKeyMixin


class ModelProfile(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "model_profiles"

    name: Mapped[str] = mapped_column(String(255))
    llm_provider: Mapped[str] = mapped_column(String(100))
    llm_model_name: Mapped[str] = mapped_column(String(255))
    embedding_provider: Mapped[str] = mapped_column(String(100))
    embedding_model_name: Mapped[str] = mapped_column(String(255))
    reranker_model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    context_limit: Mapped[int] = mapped_column(Integer)
    status: Mapped[ModelProfileStatus] = mapped_column(Enum(ModelProfileStatus), default=ModelProfileStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ModelRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "model_runs"

    model_profile_id: Mapped[str] = mapped_column(ForeignKey("model_profiles.id"), index=True)
    operation_type: Mapped[ModelOperationType] = mapped_column(Enum(ModelOperationType))
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    prompt_version_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PromptVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "prompt_versions"

    name: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(64))
    template_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Evaluation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluations"

    notebook_id: Mapped[str | None] = mapped_column(ForeignKey("notebooks.id"), nullable=True)
    model_profile_id: Mapped[str] = mapped_column(ForeignKey("model_profiles.id"))
    evaluation_type: Mapped[EvaluationType] = mapped_column(Enum(EvaluationType))
    status: Mapped[EvaluationStatus] = mapped_column(Enum(EvaluationStatus), default=EvaluationStatus.QUEUED)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvaluationResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_results"

    evaluation_id: Mapped[str] = mapped_column(ForeignKey("evaluations.id"), index=True)
    sample_key: Mapped[str] = mapped_column(String(255))
    faithfulness_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    context_recall_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    answer_relevance_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pass_fail: Mapped[bool | None] = mapped_column(nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON)


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"

    institution_id: Mapped[str | None] = mapped_column(ForeignKey("institutions.id"), nullable=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action_type: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(36))
    notebook_id: Mapped[str | None] = mapped_column(ForeignKey("notebooks.id"), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


class RetentionPolicy(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "retention_policies"

    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    resource_type: Mapped[str] = mapped_column(String(100))
    retention_days: Mapped[int] = mapped_column(Integer)
    delete_strategy: Mapped[DeleteStrategy] = mapped_column(Enum(DeleteStrategy))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
