from __future__ import annotations

import dramatiq

from app.settings import settings
from app.telemetry import configure_telemetry


_BROKER: dramatiq.Broker | None = None


def configure_broker() -> dramatiq.Broker:
    """Configure the process broker once; later calls return the same instance.

    Multiple actor modules (ingestion, batching, maintenance) call this at
    import time. A fresh broker per call would drop actors declared on the
    previous instance, which makes the worker boot without them.
    """
    global _BROKER
    if _BROKER is not None:
        return _BROKER
    configure_telemetry("curriculum-tutor-worker")
    try:
        from dramatiq.brokers.redis import RedisBroker

        broker = RedisBroker(url=settings.redis_url)
    except ModuleNotFoundError:
        from dramatiq.brokers.stub import StubBroker

        broker = StubBroker()
    dramatiq.set_broker(broker)
    _BROKER = broker
    return broker
