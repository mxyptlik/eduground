from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import AnswerType, ChatRole, MessageStatus, PolicyMode, RetrievalMode, SharingMode
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ChatSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_sessions"

    notebook_id: Mapped[str] = mapped_column(ForeignKey("notebooks.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sharing_mode: Mapped[SharingMode] = mapped_column(Enum(SharingMode), default=SharingMode.PRIVATE)
    policy_mode_snapshot: Mapped[PolicyMode] = mapped_column(Enum(PolicyMode))
    memory_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatMessage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "chat_messages"

    chat_session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[ChatRole] = mapped_column(Enum(ChatRole))
    content_markdown: Mapped[str] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(Enum(MessageStatus), default=MessageStatus.COMPLETE)
    token_input_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_output_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_run_id: Mapped[str | None] = mapped_column(ForeignKey("model_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssistantAnswer(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "assistant_answers"

    chat_message_id: Mapped[str] = mapped_column(ForeignKey("chat_messages.id"), unique=True)
    answer_type: Mapped[AnswerType] = mapped_column(Enum(AnswerType))
    citation_coverage_ratio: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    had_refusal: Mapped[bool] = mapped_column(Boolean, default=False)
    saved_to_note_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RetrievalTrace(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "retrieval_traces"

    chat_message_id: Mapped[str | None] = mapped_column(ForeignKey("chat_messages.id"), nullable=True)
    quiz_id: Mapped[str | None] = mapped_column(ForeignKey("quizzes.id"), nullable=True)
    query_text: Mapped[str] = mapped_column(Text)
    query_embedding_model: Mapped[str] = mapped_column(String(255))
    retrieval_mode: Mapped[RetrievalMode] = mapped_column(Enum(RetrievalMode))
    top_k_requested: Mapped[int] = mapped_column(Integer)
    top_k_used: Mapped[int] = mapped_column(Integer)
    filters_json: Mapped[dict] = mapped_column(JSON)
    reranker_used: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RetrievalTraceItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "retrieval_trace_items"

    retrieval_trace_id: Mapped[str] = mapped_column(ForeignKey("retrieval_traces.id"), index=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"))
    initial_score: Mapped[float] = mapped_column(Numeric(12, 6))
    rerank_score: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    rank_before: Mapped[int] = mapped_column(Integer)
    rank_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    was_used_in_context: Mapped[bool] = mapped_column(Boolean, default=False)
    dedup_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Citation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "citations"

    assistant_answer_id: Mapped[str] = mapped_column(ForeignKey("assistant_answers.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"))
    source_segment_id: Mapped[str | None] = mapped_column(ForeignKey("source_segments.id"), nullable=True)
    start_char_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slide_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slide_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote_text: Mapped[str] = mapped_column(Text)
    display_label: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

