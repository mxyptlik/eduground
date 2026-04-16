from __future__ import annotations

import json
import logging

import pytest

from app.core.logging import JsonLogFormatter, bind_log_context, clear_log_context

pytestmark = [pytest.mark.unit, pytest.mark.nonfunctional]


def test_json_log_formatter_includes_bound_request_context() -> None:
    formatter = JsonLogFormatter("api")
    logger = logging.getLogger("tests.logging")
    bind_log_context(request_id="req-123", notebook_id="nb-456")
    try:
        record = logger.makeRecord(
            logger.name,
            logging.INFO,
            __file__,
            0,
            "hello world",
            args=(),
            exc_info=None,
            extra={"extra_json": {"event": "unit_test"}},
        )
        payload = json.loads(formatter.format(record))
    finally:
        clear_log_context()

    assert payload["service"] == "api"
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "req-123"
    assert payload["notebook_id"] == "nb-456"
    assert payload["event"] == "unit_test"
