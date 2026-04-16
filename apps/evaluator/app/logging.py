from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

from app.telemetry import get_log_context


class EvaluatorJsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(get_log_context())
        extra_json = getattr(record, "extra_json", None)
        if isinstance(extra_json, dict):
            payload.update(extra_json)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging(service: str = "evaluator") -> None:
    root = logging.getLogger()
    level = getattr(logging, os.getenv("CURRICULUM_TUTOR_LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(EvaluatorJsonFormatter(service))
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
