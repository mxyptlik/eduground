from __future__ import annotations

import io
import json
import logging

import pytest

from app.logging import EvaluatorJsonFormatter
from app.telemetry import bind_context, get_metric_events, reset_test_state, stage_span


def test_stage_span_records_success_metrics() -> None:
    reset_test_state()

    with bind_context(job_id="eval-1", run_id="run-1"):
        with stage_span("dataset.load"):
            pass

    events = get_metric_events()
    assert any(event.name == "stage.executions" and event.attributes["run_id"] == "run-1" for event in events)


def test_stage_span_records_failure_metrics() -> None:
    reset_test_state()

    with pytest.raises(ValueError):
        with bind_context(job_id="eval-2", run_id="run-2"):
            with stage_span("sample.score"):
                raise ValueError("bad sample")

    events = get_metric_events()
    assert any(event.name == "stage.failures" and event.attributes["error_type"] == "ValueError" for event in events)


def test_evaluator_json_formatter_includes_telemetry_context() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(EvaluatorJsonFormatter("evaluator"))
    logger = logging.getLogger("evaluator.tests.telemetry")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    with bind_context(job_id="eval-3", run_id="run-3", stage="dataset.load"):
        logger.info("scoring", extra={"extra_json": {"sample_key": "sample-1"}})

    payload = json.loads(stream.getvalue().strip())
    assert payload["job_id"] == "eval-3"
    assert payload["run_id"] == "run-3"
    assert payload["stage"] == "dataset.load"
    assert payload["sample_key"] == "sample-1"
