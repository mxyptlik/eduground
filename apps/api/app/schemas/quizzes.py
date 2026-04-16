from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import AttemptStatus, QuizDifficulty, QuizItemType, QuizStatus
from app.schemas.common import ORMModel
from app.schemas.validators import normalize_text, validate_answer_payload, validate_uuid_list


class QuizGenerateRequest(BaseModel):
    title: str
    difficulty: QuizDifficulty = QuizDifficulty.MIXED
    source_ids: list[str] = Field(default_factory=list)
    note_ids: list[str] = Field(default_factory=list)
    module_ids: list[str] = Field(default_factory=list)
    item_count: int = Field(default=3, ge=1, le=20)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return normalize_text(value, field_name="title", max_length=255)

    @field_validator("source_ids")
    @classmethod
    def validate_source_ids(cls, value: list[str]) -> list[str]:
        return validate_uuid_list(value, field_name="source_ids", max_items=50)

    @field_validator("note_ids")
    @classmethod
    def validate_note_ids(cls, value: list[str]) -> list[str]:
        return validate_uuid_list(value, field_name="note_ids", max_items=50)

    @field_validator("module_ids")
    @classmethod
    def validate_module_ids(cls, value: list[str]) -> list[str]:
        return validate_uuid_list(value, field_name="module_ids", max_items=50)


class QuizItemResponse(ORMModel):
    id: str
    item_type: QuizItemType
    prompt_text: str
    options_json: list[str] | None
    rationale_markdown: str
    position: int


class QuizResponse(ORMModel):
    id: str
    notebook_id: str
    title: str
    difficulty: QuizDifficulty
    status: QuizStatus
    created_at: datetime
    items: list[QuizItemResponse] = Field(default_factory=list)


class QuizAttemptCreate(BaseModel):
    answers: dict[str, dict] = Field(default_factory=dict)

    @field_validator("answers")
    @classmethod
    def validate_answers(cls, value: dict[str, dict]) -> dict[str, dict]:
        return validate_answer_payload(value)


class QuizAttemptSubmit(BaseModel):
    answers: dict[str, dict]

    @field_validator("answers")
    @classmethod
    def validate_answers(cls, value: dict[str, dict]) -> dict[str, dict]:
        return validate_answer_payload(value)


class QuizAttemptItemResultResponse(ORMModel):
    quiz_item_id: str
    submitted_answer_json: dict
    correct_answer_json: dict
    is_correct: bool | None = None
    feedback_markdown: str | None = None


class QuizAttemptResponse(ORMModel):
    id: str
    quiz_id: str
    user_id: str
    started_at: datetime
    submitted_at: datetime | None = None
    score_numeric: float | None = None
    score_percent: float | None = None
    status: AttemptStatus
    passed: bool | None = None
    correct_count: int | None = None
    total_items: int | None = None
    result_comment: str | None = None
    results: list[QuizAttemptItemResultResponse] = Field(default_factory=list)
