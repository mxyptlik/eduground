from __future__ import annotations

from app.core.config import settings
from app.db.base import Base
from app.db.session import engine
from app.models import content, curriculum, identity, learning, operations, tutoring


def initialize_database() -> None:
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)


def reset_database() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
