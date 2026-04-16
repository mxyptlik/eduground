from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import AttemptStatus, NoteType, NoteVisibility, QuizDifficulty, QuizGenerationMode, QuizItemType, QuizStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Note(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notes"

    notebook_id: Mapped[str] = mapped_column(ForeignKey("notebooks.id"), index=True)
    author_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    content_markdown: Mapped[str] = mapped_column(Text)
    note_type: Mapped[NoteType] = mapped_column(Enum(NoteType), default=NoteType.MANUAL)
    source_chat_message_id: Mapped[str | None] = mapped_column(ForeignKey("chat_messages.id"), nullable=True)
    converted_source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    visibility: Mapped[NoteVisibility] = mapped_column(Enum(NoteVisibility), default=NoteVisibility.PRIVATE)


class NoteCitation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "note_citations"

    note_id: Mapped[str] = mapped_column(ForeignKey("notes.id"), index=True)
    citation_id: Mapped[str] = mapped_column(ForeignKey("citations.id"))


class Quiz(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quizzes"

    notebook_id: Mapped[str] = mapped_column(ForeignKey("notebooks.id"), index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(255))
    difficulty: Mapped[QuizDifficulty] = mapped_column(Enum(QuizDifficulty), default=QuizDifficulty.MIXED)
    scope_json: Mapped[dict] = mapped_column(JSON)
    generation_mode: Mapped[QuizGenerationMode] = mapped_column(
        Enum(QuizGenerationMode),
        default=QuizGenerationMode.TUTOR_GENERATED,
    )
    status: Mapped[QuizStatus] = mapped_column(Enum(QuizStatus), default=QuizStatus.DRAFT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class QuizItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quiz_items"

    quiz_id: Mapped[str] = mapped_column(ForeignKey("quizzes.id"), index=True)
    item_type: Mapped[QuizItemType] = mapped_column(Enum(QuizItemType))
    prompt_text: Mapped[str] = mapped_column(Text)
    options_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    correct_answer_json: Mapped[dict] = mapped_column(JSON)
    rationale_markdown: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class QuizItemCitation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quiz_item_citations"

    quiz_item_id: Mapped[str] = mapped_column(ForeignKey("quiz_items.id"), index=True)
    citation_id: Mapped[str | None] = mapped_column(ForeignKey("citations.id"), nullable=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"))


class QuizAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quiz_attempts"

    quiz_id: Mapped[str] = mapped_column(ForeignKey("quizzes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    score_numeric: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    score_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    status: Mapped[AttemptStatus] = mapped_column(Enum(AttemptStatus), default=AttemptStatus.IN_PROGRESS)


class QuizAttemptItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quiz_attempt_items"

    quiz_attempt_id: Mapped[str] = mapped_column(ForeignKey("quiz_attempts.id"), index=True)
    quiz_item_id: Mapped[str] = mapped_column(ForeignKey("quiz_items.id"))
    submitted_answer_json: Mapped[dict] = mapped_column(JSON)
    is_correct: Mapped[bool | None] = mapped_column(nullable=True)
    feedback_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    earned_points: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TopicMastery(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "topic_mastery"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    notebook_id: Mapped[str] = mapped_column(ForeignKey("notebooks.id"), index=True)
    module_id: Mapped[str | None] = mapped_column(ForeignKey("curriculum_modules.id"), nullable=True)
    learning_objective_id: Mapped[str | None] = mapped_column(ForeignKey("learning_objectives.id"), nullable=True)
    mastery_score: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

