from __future__ import annotations

import os
import time

from app.bootstrap import configure_broker
from app.jobs.maintenance import cleanup_deleted_sources
from app.logging import configure_logging, get_logger

configure_logging("worker")
logger = get_logger("worker.jobs.scheduler")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid integer env var; using default", extra={"extra_json": {"name": name, "value": raw, "default": default}})
        return default
    return value if value > 0 else default


def main() -> None:
    configure_broker()
    interval_seconds = _env_int("CURRICULUM_TUTOR_MAINTENANCE_INTERVAL_SECONDS", 3600)
    startup_delay_seconds = _env_int("CURRICULUM_TUTOR_MAINTENANCE_STARTUP_DELAY_SECONDS", 30)

    logger.info(
        "Starting maintenance scheduler",
        extra={
            "extra_json": {
                "interval_seconds": interval_seconds,
                "startup_delay_seconds": startup_delay_seconds,
            }
        },
    )

    if startup_delay_seconds:
        time.sleep(startup_delay_seconds)

    while True:
        cleanup_deleted_sources.send()
        logger.info("Queued cleanup_deleted_sources job")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
