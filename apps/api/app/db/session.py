from __future__ import annotations

from collections.abc import Generator
from time import perf_counter

from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _normalize_sqlalchemy_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    if database_url.startswith("postgresql+psycopg2://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql+psycopg2://")
    return database_url


database_url = _normalize_sqlalchemy_database_url(settings.database_url)

if database_url.startswith("sqlite"):
    engine = create_engine(
        database_url,
        future=True,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
else:
    engine = create_engine(
        database_url,
        future=True,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
    )
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


@event.listens_for(engine, "checkout")
def _record_checkout(_connection, record, _proxy):
    record.info["eduground_checked_out_at"] = perf_counter()


@event.listens_for(engine, "checkin")
def _record_checkin(_connection, record):
    started = record.info.pop("eduground_checked_out_at", None)
    if started is not None:
        from app.core.observability import histogram
        histogram("eduground_db_connection_hold_ms", (perf_counter() - started) * 1000)


def get_db_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
