from __future__ import annotations

import dramatiq

from app.settings import settings
from app.telemetry import configure_telemetry


def configure_broker() -> dramatiq.Broker:
    configure_telemetry("curriculum-tutor-worker")
    try:
        from dramatiq.brokers.redis import RedisBroker

        broker = RedisBroker(url=settings.redis_url)
    except ModuleNotFoundError:
        from dramatiq.brokers.stub import StubBroker

        broker = StubBroker()
    dramatiq.set_broker(broker)
    return broker
