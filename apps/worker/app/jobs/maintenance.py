from __future__ import annotations

from uuid import uuid4

import dramatiq

from app.bootstrap import configure_broker
from app.logging import configure_logging, get_logger
from app.telemetry import bind_context, configure_telemetry, stage_span

configure_logging("worker")
configure_telemetry("curriculum-tutor-worker")
configure_broker()
logger = get_logger("worker.jobs.maintenance")


@dramatiq.actor
def cleanup_deleted_sources() -> dict[str, str]:
    job_id = "maintenance:cleanup_deleted_sources"
    run_id = uuid4().hex
    with bind_context(job_id=job_id, run_id=run_id):
        with stage_span("job.cleanup_deleted_sources", logger=logger):
            logger.info("Scheduled deleted-source cleanup")
            return {"status": "cleanup_scheduled"}
