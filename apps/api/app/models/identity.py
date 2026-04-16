from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import AuthProvider, DeploymentMode, InstitutionRole, PrivacyMode, UserStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Institution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "institutions"

    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    external_organization_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    organization_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deployment_mode: Mapped[DeploymentMode] = mapped_column(Enum(DeploymentMode))
    sso_config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    data_retention_policy_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    privacy_mode: Mapped[PrivacyMode] = mapped_column(Enum(PrivacyMode), default=PrivacyMode.STANDARD)

    users: Mapped[list["User"]] = relationship(back_populates="institution")
    memberships: Mapped[list["InstitutionMembership"]] = relationship(back_populates="institution")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("auth_provider", "external_subject_id", name="uq_user_auth_provider_external_subject"),)

    institution_id: Mapped[str | None] = mapped_column(ForeignKey("institutions.id"), nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    auth_provider: Mapped[AuthProvider] = mapped_column(Enum(AuthProvider), default=AuthProvider.LOCAL)
    external_subject_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    display_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.ACTIVE)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    institution: Mapped[Institution | None] = relationship(back_populates="users")
    institution_memberships: Mapped[list["InstitutionMembership"]] = relationship(back_populates="user")


class RoleReference(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), unique=True)


class InstitutionMembership(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "institution_memberships"
    __table_args__ = (UniqueConstraint("institution_id", "user_id", name="uq_institution_membership"),)

    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    external_membership_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    membership_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    role: Mapped[InstitutionRole] = mapped_column(Enum(InstitutionRole))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    institution: Mapped[Institution] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="institution_memberships")
