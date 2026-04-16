from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.core.observability import current_trace_context

_log_context: ContextVar[dict[str, Any]] = ContextVar("api_log_context", default={})


def bind_log_context(**values: Any) -> None:
    context = dict(_log_context.get())
    context.update({key: value for key, value in values.items() if value is not None})
    _log_context.set(context)


def clear_log_context() -> None:
    _log_context.set({})


def get_log_context() -> dict[str, Any]:
    return dict(_log_context.get())


class JsonLogFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "environment": settings.app_env,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(get_log_context())
        payload.update(current_trace_context())
        extra_json = getattr(record, "extra_json", None)
        if isinstance(extra_json, dict):
            payload.update(extra_json)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=True)


class PlainLogFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s [%(service)s] %(message)s")
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        record.service = self.service  # type: ignore[attr-defined]
        return super().format(record)


def configure_logging(service: str = "api") -> None:
    root = logging.getLogger()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter(service) if settings.log_json else PlainLogFormatter(service))
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
