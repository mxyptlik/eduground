from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.enums import AuthProvider, InstitutionRole
from app.models.identity import Institution, InstitutionMembership, User


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_email(self, email: str) -> User | None:
        return self.db.query(User).filter(User.email == email).first()

    def get_by_external_subject_id(self, external_subject_id: str, *, auth_provider: AuthProvider = AuthProvider.CLERK) -> User | None:
        return (
            self.db.query(User)
            .filter(User.auth_provider == auth_provider, User.external_subject_id == external_subject_id)
            .first()
        )

    def get_membership(self, user_id: str) -> InstitutionMembership | None:
        return (
            self.db.query(InstitutionMembership)
            .filter(InstitutionMembership.user_id == user_id)
            .order_by(InstitutionMembership.created_at.desc())
            .first()
        )

    def list_memberships_for_user(self, user_id: str) -> list[InstitutionMembership]:
        return (
            self.db.query(InstitutionMembership)
            .filter(InstitutionMembership.user_id == user_id)
            .order_by(InstitutionMembership.created_at.desc())
            .all()
        )

    def get_membership_for_institution_user(self, institution_id: str, user_id: str) -> InstitutionMembership | None:
        return (
            self.db.query(InstitutionMembership)
            .filter(
                InstitutionMembership.institution_id == institution_id,
                InstitutionMembership.user_id == user_id,
            )
            .first()
        )

    def get_membership_by_external_membership_id(self, external_membership_id: str) -> InstitutionMembership | None:
        return (
            self.db.query(InstitutionMembership)
            .filter(InstitutionMembership.external_membership_id == external_membership_id)
            .first()
        )

    def delete_membership(self, membership: InstitutionMembership) -> None:
        self.db.delete(membership)
        self.db.flush()

    def get_by_slug(self, slug: str) -> Institution | None:
        return self.db.query(Institution).filter(Institution.slug == slug).first()

    def get_institution_by_external_organization_id(self, external_organization_id: str) -> Institution | None:
        return (
            self.db.query(Institution)
            .filter(Institution.external_organization_id == external_organization_id)
            .first()
        )

    def institution_slug_exists(self, slug: str) -> bool:
        return self.db.query(func.count(Institution.id)).filter(Institution.slug == slug).scalar() > 0

    def save_user(self, user: User) -> User:
        self.db.add(user)
        self.db.flush()
        return user

    def save_institution(self, institution: Institution) -> Institution:
        self.db.add(institution)
        self.db.flush()
        return institution

    def save_membership(self, membership: InstitutionMembership) -> InstitutionMembership:
        self.db.add(membership)
        self.db.flush()
        return membership

    def upsert_clerk_user(
        self,
        *,
        external_subject_id: str,
        email: str,
        display_name: str,
        institution_id: str | None,
        status,
        user_metadata_json: dict | None = None,
    ) -> User:
        user = self.get_by_external_subject_id(external_subject_id, auth_provider=AuthProvider.CLERK)
        if user is None:
            user = self.get_by_email(email)
        if user is None:
            user = User(
                institution_id=institution_id,
                email=email,
                password_hash=None,
                auth_provider=AuthProvider.CLERK,
                external_subject_id=external_subject_id,
                display_name=display_name,
                status=status,
                user_metadata_json=user_metadata_json,
            )
        else:
            user.institution_id = institution_id
            user.email = email
            user.auth_provider = AuthProvider.CLERK
            user.external_subject_id = external_subject_id
            user.display_name = display_name
            user.status = status
            user.user_metadata_json = user_metadata_json
        return self.save_user(user)

    def upsert_clerk_institution(
        self,
        *,
        external_organization_id: str,
        name: str,
        slug: str,
        deployment_mode,
        privacy_mode,
        organization_metadata_json: dict | None = None,
    ) -> Institution:
        institution = self.get_institution_by_external_organization_id(external_organization_id)
        if institution is None:
            institution = self.get_by_slug(slug)
        if institution is None:
            institution = Institution(
                name=name,
                slug=slug,
                external_organization_id=external_organization_id,
                organization_metadata_json=organization_metadata_json,
                deployment_mode=deployment_mode,
                sso_config_json=None,
                data_retention_policy_json=None,
                privacy_mode=privacy_mode,
            )
        else:
            institution.name = name
            institution.slug = slug
            institution.external_organization_id = external_organization_id
            institution.organization_metadata_json = organization_metadata_json
            institution.deployment_mode = deployment_mode
            institution.privacy_mode = privacy_mode
        return self.save_institution(institution)

    def upsert_clerk_membership(
        self,
        *,
        institution_id: str,
        user_id: str,
        role: InstitutionRole,
        created_at,
        external_membership_id: str | None = None,
        membership_metadata_json: dict | None = None,
    ) -> InstitutionMembership:
        membership = None
        if external_membership_id:
            membership = self.get_membership_by_external_membership_id(external_membership_id)
        if membership is None:
            membership = self.get_membership_for_institution_user(institution_id, user_id)
        if membership is None:
            membership = InstitutionMembership(
                institution_id=institution_id,
                user_id=user_id,
                role=role,
                created_at=created_at,
                external_membership_id=external_membership_id,
                membership_metadata_json=membership_metadata_json,
            )
        else:
            membership.role = role
            membership.created_at = created_at
            membership.external_membership_id = external_membership_id
            membership.membership_metadata_json = membership_metadata_json
        return self.save_membership(membership)
