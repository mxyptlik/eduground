from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import InstitutionRole


class OrganizationMembershipSummary(BaseModel):
    institution_id: str
    institution_name: str
    institution_slug: str
    external_organization_id: str | None = None
    role: InstitutionRole


class AuthUser(BaseModel):
    id: str
    email: EmailStr
    display_name: str
    institution_id: str | None
    role: InstitutionRole | None = None
    external_subject_id: str | None = None
    active_organization_id: str | None = None
    active_organization_slug: str | None = None
    memberships: list[OrganizationMembershipSummary] = Field(default_factory=list)


class ClerkWebhookResponse(BaseModel):
    status: str
    processed_event_type: str
