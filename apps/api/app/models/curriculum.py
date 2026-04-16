from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import CourseOfferingStatus, ModuleType, NotebookMembershipRole, NotebookVisibility, PolicyMode
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Course(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("institution_id", "course_code", name="uq_course_code_per_institution"),)

    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    course_code: Mapped[str] = mapped_column(String(64))
    course_name: Mapped[str] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)


class CourseOffering(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "course_offerings"
    __table_args__ = (UniqueConstraint("course_id", "term", "section", name="uq_course_offering"),)

    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    term: Mapped[str] = mapped_column(String(128))
    section: Mapped[str] = mapped_column(String(128))
    instructor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    lms_course_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[CourseOfferingStatus] = mapped_column(Enum(CourseOfferingStatus), default=CourseOfferingStatus.DRAFT)


class Notebook(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notebooks"

    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    course_offering_id: Mapped[str | None] = mapped_column(ForeignKey("course_offerings.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    visibility: Mapped[NotebookVisibility] = mapped_column(Enum(NotebookVisibility), default=NotebookVisibility.PRIVATE)
    policy_mode: Mapped[PolicyMode] = mapped_column(Enum(PolicyMode), default=PolicyMode.TEACHING, index=True)
    model_pin_id: Mapped[str | None] = mapped_column(ForeignKey("model_profiles.id"), nullable=True)
    embedding_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_version: Mapped[int] = mapped_column(Integer, default=1)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memberships: Mapped[list["NotebookMembership"]] = relationship(back_populates="notebook")
    modules: Mapped[list["CurriculumModule"]] = relationship(back_populates="notebook")
    learning_objectives: Mapped[list["LearningObjective"]] = relationship(back_populates="notebook")


class NotebookMembership(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notebook_memberships"
    __table_args__ = (UniqueConstraint("notebook_id", "user_id", name="uq_notebook_membership"),)

    notebook_id: Mapped[str] = mapped_column(ForeignKey("notebooks.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[NotebookMembershipRole] = mapped_column(Enum(NotebookMembershipRole))
    granted_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    notebook: Mapped[Notebook] = relationship(back_populates="memberships")


class CurriculumModule(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "curriculum_modules"

    notebook_id: Mapped[str] = mapped_column(ForeignKey("notebooks.id"), index=True)
    parent_module_id: Mapped[str | None] = mapped_column(ForeignKey("curriculum_modules.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    module_type: Mapped[ModuleType] = mapped_column(Enum(ModuleType))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    notebook: Mapped[Notebook] = relationship(back_populates="modules")


class LearningObjective(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "learning_objectives"

    notebook_id: Mapped[str] = mapped_column(ForeignKey("notebooks.id"), index=True)
    module_id: Mapped[str | None] = mapped_column(ForeignKey("curriculum_modules.id"), nullable=True, index=True)
    code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    difficulty_level: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    notebook: Mapped[Notebook] = relationship(back_populates="learning_objectives")

