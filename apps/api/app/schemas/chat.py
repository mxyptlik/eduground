from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.models.enums import AnswerType, PolicyMode
from app.schemas.common import ORMModel
from app.schemas.validators import normalize_optional_text, normalize_text, validate_uuid_list


class ChatSessionCreate(BaseModel):
    title: str | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return normalize_optional_text(value, field_name="title", max_length=255)


class ChatSessionResponse(ORMModel):
    id: str
    notebook_id: str
    user_id: str
    title: str | None
    policy_mode_snapshot: PolicyMode


class ChatMessageCreate(BaseModel):
    content_markdown: str = Field(min_length=1)
    selected_source_ids: list[str] = Field(default_factory=list)
    selected_module_ids: list[str] = Field(default_factory=list)

    @field_validator("content_markdown")
    @classmethod
    def validate_content(cls, value: str) -> str:
        return normalize_text(
            value,
            field_name="content_markdown",
            max_length=8_000,
            allow_newlines=True,
            allow_dangerous_markup=False,
        )

    @field_validator("selected_source_ids")
    @classmethod
    def validate_selected_source_ids(cls, value: list[str]) -> list[str]:
        return validate_uuid_list(value, field_name="selected_source_ids", max_items=50)

    @field_validator("selected_module_ids")
    @classmethod
    def validate_selected_module_ids(cls, value: list[str]) -> list[str]:
        return validate_uuid_list(value, field_name="selected_module_ids", max_items=50)


class CitationResponse(BaseModel):
    id: str
    source_id: str
    chunk_id: str
    source_segment_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    slide_start: int | None = None
    slide_end: int | None = None
    quote_text: str
    display_label: str


class RetrievalTraceResponse(BaseModel):
    id: str
    query_text: str
    retrieval_mode: str
    filters_json: dict
    top_k_requested: int
    top_k_used: int
    reranker_used: bool
    latency_ms: int
    items: list["RetrievalTraceItemResponse"] = Field(default_factory=list)


class RetrievalTraceItemResponse(BaseModel):
    chunk_id: str
    source_id: str
    initial_score: float
    rank_before: int
    rank_after: int | None = None
    was_used_in_context: bool


class AnswerResponse(BaseModel):
    answer_type: AnswerType
    content_markdown: str
    citations: list[CitationResponse]
    refusal_reason: str | None = None
    retrieval_trace: RetrievalTraceResponse | None = None


class StreamSourcePreview(BaseModel):
    source_id: str
    chunk_id: str
    display_label: str
    page_start: int | None = None
    page_end: int | None = None
    slide_start: int | None = None
    slide_end: int | None = None
    quote_text: str


class StreamStatusPayload(BaseModel):
    phase: str
    message: str


class ChatStreamEvent(BaseModel):
    type: str
    data: dict | str | AnswerResponse | list[StreamSourcePreview] | StreamStatusPayload
