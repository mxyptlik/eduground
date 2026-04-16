from __future__ import annotations

from pydantic import BaseModel, field_validator

from app.models.enums import NotebookMembershipRole, NotebookVisibility, PolicyMode
from app.schemas.common import ORMModel
from app.schemas.validators import normalize_optional_text, normalize_text, validate_uuid_string


class NotebookCreate(BaseModel):
    title: str
    description: str | None = None
    visibility: NotebookVisibility = NotebookVisibility.PRIVATE
    policy_mode: PolicyMode = PolicyMode.TEACHING
    course_offering_id: str | None = None

    @field_validator("course_offering_id")
    @classmethod
    def validate_optional_ids(cls, value: str | None, info) -> str | None:
        return validate_uuid_string(value, field_name=info.field_name)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return normalize_text(value, field_name="title", max_length=255)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        return normalize_optional_text(value, field_name="description", max_length=2000, allow_newlines=True)


class NotebookUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    visibility: NotebookVisibility | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return normalize_optional_text(value, field_name="title", max_length=255)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        return normalize_optional_text(value, field_name="description", max_length=2000, allow_newlines=True)


class NotebookPolicyUpdate(BaseModel):
    policy_mode: PolicyMode


class NotebookMembershipCreate(BaseModel):
    user_id: str
    role: NotebookMembershipRole

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str) -> str:
        normalized = validate_uuid_string(value, field_name="user_id")
        assert normalized is not None
        return normalized


class NotebookResponse(ORMModel):
    id: str
    institution_id: str
    title: str
    description: str | None
    owner_user_id: str
    visibility: NotebookVisibility
    policy_mode: PolicyMode
    course_offering_id: str | None


class NotebookListItemResponse(NotebookResponse):
    source_count: int = 0
    learner_count: int = 0
