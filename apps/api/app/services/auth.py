from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.logging import get_log_context
from app.integrations.auth.clerk import ClerkSession, get_clerk_management_client
from app.models.enums import AuthProvider, DeploymentMode, InstitutionRole, PrivacyMode, UserStatus
from app.models.identity import Institution, InstitutionMembership, User
from app.models.operations import AuditLog
from app.repositories.auth import UserRepository
from app.schemas.auth import AuthUser, OrganizationMembershipSummary
from app.services.audit import AuditService


class AuthService:
    _LAST_LOGIN_TOUCH_INTERVAL_SECONDS = 600

    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.audit = AuditService(db)
        self.clerk = get_clerk_management_client()

    def _stage_audit(
        self,
        *,
        actor_user_id: str | None,
        action_type: str,
        resource_type: str,
        resource_id: str,
        notebook_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        log_context = get_log_context()
        metadata_json = dict(metadata or {})
        if log_context.get("request_id"):
            metadata_json.setdefault("request_id", log_context["request_id"])
        if log_context.get("path"):
            metadata_json.setdefault("path", log_context["path"])
        if log_context.get("method"):
            metadata_json.setdefault("method", log_context["method"])
        self.db.add(
            AuditLog(
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
        )

    def sync_clerk_session(self, session: ClerkSession, *, active_organization_id: str | None = None) -> tuple[User, InstitutionMembership | None]:
        resolved_org_id = active_organization_id or session.active_organization_id
        now = datetime.now(UTC)
        did_mutate = False
        institution: Institution | None = (
            self.users.get_institution_by_external_organization_id(resolved_org_id) if resolved_org_id else None
        )
        user = self.users.get_by_external_subject_id(session.subject_id, auth_provider=AuthProvider.CLERK)

        if user is None:
            user_payload = self.clerk.get_user(session.subject_id)
            email = self._extract_primary_email(user_payload)
            display_name = self._extract_display_name(user_payload, email)
            user = self.users.upsert_clerk_user(
                external_subject_id=session.subject_id,
                email=email,
                display_name=display_name,
                institution_id=institution.id if institution else None,
                status=UserStatus.ACTIVE,
                user_metadata_json=self._user_metadata(user_payload),
            )
            did_mutate = True
        else:
            next_institution_id = institution.id if institution else user.institution_id
            if user.institution_id != next_institution_id:
                user.institution_id = next_institution_id
                did_mutate = True
            if user.status != UserStatus.ACTIVE:
                user.status = UserStatus.ACTIVE
                did_mutate = True

        if resolved_org_id and institution is None:
            organization_payload = self.clerk.get_organization(resolved_org_id)
            institution = self._upsert_institution(organization_payload)
            did_mutate = True
            if user.institution_id != institution.id:
                user.institution_id = institution.id
                did_mutate = True

        membership: InstitutionMembership | None = None
        if institution is not None:
            desired_role = self._map_clerk_role(session.organization_role)
            desired_external_membership_id = self._coerce_optional_string(session.claims.get("org_membership_id"))
            desired_membership_metadata = {
                "org_role": session.organization_role,
                "org_slug": session.organization_slug,
                "claims": {
                    "org_id": resolved_org_id,
                    "org_role": session.organization_role,
                    "org_slug": session.organization_slug,
                },
            }
            membership = self.users.get_membership_for_institution_user(institution.id, user.id)
            if membership is None:
                membership = self.users.upsert_clerk_membership(
                    institution_id=institution.id,
                    user_id=user.id,
                    role=desired_role,
                    created_at=now,
                    external_membership_id=desired_external_membership_id,
                    membership_metadata_json=desired_membership_metadata,
                )
                did_mutate = True
            else:
                membership_changed = False
                if membership.role != desired_role:
                    membership.role = desired_role
                    membership_changed = True
                if membership.external_membership_id != desired_external_membership_id:
                    membership.external_membership_id = desired_external_membership_id
                    membership_changed = True
                if membership.membership_metadata_json != desired_membership_metadata:
                    membership.membership_metadata_json = desired_membership_metadata
                    membership_changed = True
                if membership_changed:
                    self.db.add(membership)
                    did_mutate = True

        should_touch_last_login = (
            user.last_login_at is None
            or (now - user.last_login_at).total_seconds() >= self._LAST_LOGIN_TOUCH_INTERVAL_SECONDS
        )
        if should_touch_last_login:
            user.last_login_at = now
            did_mutate = True

        if did_mutate:
            self.db.add(user)
            self._stage_audit(
                actor_user_id=user.id,
                action_type="auth.clerk.sync",
                resource_type="user",
                resource_id=user.id,
                metadata={
                    "external_subject_id": session.subject_id,
                    "active_organization_id": resolved_org_id,
                    "membership_id": membership.id if membership is not None else None,
                },
            )
            self.db.commit()
            self.db.refresh(user)
            if membership is not None:
                self.db.refresh(membership)
        return user, membership

    def build_auth_user(
        self,
        *,
        user: User,
        active_membership: InstitutionMembership | None,
        active_organization_id: str | None,
    ) -> AuthUser:
        memberships = self.users.list_memberships_for_user(user.id)
        institutions = {
            institution.id: institution
            for institution in self.db.query(Institution).filter(Institution.id.in_([m.institution_id for m in memberships])).all()
        }
        membership_summaries = [
            OrganizationMembershipSummary(
                institution_id=membership.institution_id,
                institution_name=institutions[membership.institution_id].name,
                institution_slug=institutions[membership.institution_id].slug,
                external_organization_id=institutions[membership.institution_id].external_organization_id,
                role=membership.role,
            )
            for membership in memberships
            if membership.institution_id in institutions
        ]
        active_institution = institutions.get(active_membership.institution_id) if active_membership else None
        return AuthUser(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            institution_id=user.institution_id,
            role=active_membership.role if active_membership else None,
            external_subject_id=user.external_subject_id,
            active_organization_id=active_organization_id,
            active_organization_slug=active_institution.slug if active_institution else None,
            memberships=membership_summaries,
        )

    def handle_clerk_webhook(self, event_type: str, payload: dict[str, Any]) -> None:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Clerk webhook payload missing event data")

        if event_type in {"user.created", "user.updated"}:
            self._handle_user_event(data)
        elif event_type in {"organization.created", "organization.updated"}:
            self._upsert_institution(data)
            self.db.commit()
        elif event_type in {"organizationMembership.created", "organizationMembership.updated"}:
            self._handle_membership_event(data)
        elif event_type == "organizationMembership.deleted":
            self._handle_membership_deleted(data)
        else:
            return

        self.audit.record(
            actor_user_id=None,
            action_type="auth.clerk.webhook",
            resource_type="clerk_event",
            resource_id=event_type,
            metadata={"event_type": event_type},
        )

        self.db.commit()

    def _handle_user_event(self, user_payload: dict[str, Any]) -> None:
        external_subject_id = self._required_string(user_payload, "id", "Clerk user payload missing id")
        email = self._extract_primary_email(user_payload)
        display_name = self._extract_display_name(user_payload, email)
        self.users.upsert_clerk_user(
            external_subject_id=external_subject_id,
            email=email,
            display_name=display_name,
            institution_id=None,
            status=UserStatus.ACTIVE,
            user_metadata_json=self._user_metadata(user_payload),
        )

    def _handle_membership_event(self, membership_payload: dict[str, Any]) -> None:
        organization = membership_payload.get("organization")
        if not isinstance(organization, dict):
            organization_id = self._required_string(
                membership_payload,
                "organization_id",
                "Clerk organization membership payload missing organization_id",
            )
            organization = self.clerk.get_organization(organization_id)
        institution = self._upsert_institution(organization)

        public_user_data = membership_payload.get("public_user_data")
        if isinstance(public_user_data, dict):
            external_subject_id = self._required_string(public_user_data, "user_id", "Clerk membership payload missing user id")
            email = self._required_string(public_user_data, "identifier", "Clerk membership payload missing user email")
            display_name = (
                self._coerce_optional_string(public_user_data.get("first_name"))
                or self._coerce_optional_string(public_user_data.get("last_name"))
                or email.split("@", maxsplit=1)[0]
            )
            user = self.users.upsert_clerk_user(
                external_subject_id=external_subject_id,
                email=email,
                display_name=display_name,
                institution_id=institution.id,
                status=UserStatus.ACTIVE,
                user_metadata_json=public_user_data,
            )
        else:
            user_id = self._required_string(membership_payload, "public_user_id", "Clerk membership payload missing user id")
            user_payload = self.clerk.get_user(user_id)
            user = self.users.upsert_clerk_user(
                external_subject_id=user_id,
                email=self._extract_primary_email(user_payload),
                display_name=self._extract_display_name(user_payload, self._extract_primary_email(user_payload)),
                institution_id=institution.id,
                status=UserStatus.ACTIVE,
                user_metadata_json=self._user_metadata(user_payload),
            )

        membership = self.users.upsert_clerk_membership(
            institution_id=institution.id,
            user_id=user.id,
            role=self._map_clerk_role(self._coerce_optional_string(membership_payload.get("role"))),
            created_at=self._coerce_datetime(membership_payload.get("created_at")),
            external_membership_id=self._coerce_optional_string(membership_payload.get("id")),
            membership_metadata_json=membership_payload,
        )
        self.audit.record(
            actor_user_id=user.id,
            action_type="organization.membership.sync",
            resource_type="institution_membership",
            resource_id=membership.id,
            metadata={"institution_id": institution.id, "external_membership_id": membership.external_membership_id},
        )

    def _handle_membership_deleted(self, membership_payload: dict[str, Any]) -> None:
        external_membership_id = self._coerce_optional_string(membership_payload.get("id"))
        membership = (
            self.users.get_membership_by_external_membership_id(external_membership_id)
            if external_membership_id
            else None
        )
        if membership is not None:
            self.users.delete_membership(membership)
            self.audit.record(
                actor_user_id=membership.user_id,
                action_type="organization.membership.delete",
                resource_type="institution_membership",
                resource_id=membership.id,
                metadata={"institution_id": membership.institution_id},
            )

    def _upsert_institution(self, organization_payload: dict[str, Any]) -> Institution:
        organization_id = self._required_string(organization_payload, "id", "Clerk organization payload missing id")
        name = self._required_string(organization_payload, "name", "Clerk organization payload missing name")
        slug = self._unique_slug(
            self._coerce_optional_string(organization_payload.get("slug")) or self._slugify(name),
            external_organization_id=organization_id,
        )
        return self.users.upsert_clerk_institution(
            external_organization_id=organization_id,
            name=name,
            slug=slug,
            deployment_mode=DeploymentMode.CLOUD,
            privacy_mode=PrivacyMode.STANDARD,
            organization_metadata_json=organization_payload,
        )

    def _unique_slug(self, base_slug: str, *, external_organization_id: str) -> str:
        candidate = base_slug or "organization"
        institution = self.users.get_institution_by_external_organization_id(external_organization_id)
        if institution is not None:
            return candidate
        if not self.users.institution_slug_exists(candidate):
            return candidate
        suffix = 2
        while self.users.institution_slug_exists(f"{candidate}-{suffix}"):
            suffix += 1
        return f"{candidate}-{suffix}"

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
        return slug or "organization"

    def _extract_primary_email(self, user_payload: dict[str, Any]) -> str:
        primary_email_id = self._coerce_optional_string(user_payload.get("primary_email_address_id"))
        email_addresses = user_payload.get("email_addresses") or []
        if isinstance(email_addresses, list):
            for entry in email_addresses:
                if not isinstance(entry, dict):
                    continue
                email_address = self._coerce_optional_string(entry.get("email_address"))
                if not email_address:
                    continue
                if primary_email_id is None or entry.get("id") == primary_email_id:
                    return email_address.lower()
            for entry in email_addresses:
                if isinstance(entry, dict):
                    email_address = self._coerce_optional_string(entry.get("email_address"))
                    if email_address:
                        return email_address.lower()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Clerk user profile did not include an email address")

    def _extract_display_name(self, user_payload: dict[str, Any], email: str) -> str:
        full_name = self._coerce_optional_string(user_payload.get("full_name"))
        if full_name:
            return full_name
        first_name = self._coerce_optional_string(user_payload.get("first_name"))
        last_name = self._coerce_optional_string(user_payload.get("last_name"))
        if first_name or last_name:
            return " ".join(part for part in (first_name, last_name) if part)
        username = self._coerce_optional_string(user_payload.get("username"))
        if username:
            return username
        return email.split("@", maxsplit=1)[0]

    def _user_metadata(self, user_payload: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for key in ("public_metadata", "private_metadata", "unsafe_metadata", "profile_image_url"):
            value = user_payload.get(key)
            if value is not None:
                metadata[key] = value
        return metadata

    def _map_clerk_role(self, role: str | None) -> InstitutionRole:
        normalized = (role or "org:member").strip().lower()
        mapping = {
            "org:admin": InstitutionRole.ADMIN,
            "admin": InstitutionRole.ADMIN,
            "org:member": InstitutionRole.STUDENT,
            "member": InstitutionRole.STUDENT,
            "student": InstitutionRole.STUDENT,
            "org:student": InstitutionRole.STUDENT,
            "instructor": InstitutionRole.INSTRUCTOR,
            "org:instructor": InstitutionRole.INSTRUCTOR,
            "ta": InstitutionRole.TA,
            "org:ta": InstitutionRole.TA,
        }
        return mapping.get(normalized, InstitutionRole.STUDENT)

    def _required_string(self, payload: dict[str, Any], key: str, message: str) -> str:
        value = self._coerce_optional_string(payload.get(key))
        if value is None:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message)
        return value

    def _coerce_optional_string(self, value: Any) -> str | None:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return None

    def _coerce_datetime(self, value: Any) -> datetime:
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000.0
            return datetime.fromtimestamp(timestamp, UTC)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return datetime.now(UTC)
        return datetime.now(UTC)
