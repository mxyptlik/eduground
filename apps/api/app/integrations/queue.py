from __future__ import annotations

from dramatiq import Message

from app.core.config import settings
from app.core.errors import DependencyUnavailableError

INGESTION_ACTOR_NAME = "process_source_ingestion_job"


def enqueue_source_ingestion_job(*, job_id: str, source_id: str, source_version_id: str, institution_id: str) -> None:
    try:
        from dramatiq.brokers.redis import RedisBroker

        broker = RedisBroker(url=settings.redis_url)
        broker.enqueue(
            Message(
                queue_name="default",
                actor_name=INGESTION_ACTOR_NAME,
                args=(job_id, source_id, source_version_id, institution_id),
                kwargs={},
                options={},
            )
        )
    except Exception as exc:  # pragma: no cover - runtime queue failure
        raise DependencyUnavailableError(
            code="ingestion_queue_unavailable",
            message=f"Could not enqueue ingestion job: {exc}",
            provider="redis",
        ) from exc
