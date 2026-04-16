from __future__ import annotations

import logging
import os
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar, Token
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Iterator

try:  # pragma: no cover - exercised when opentelemetry is installed
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in environments without the dependency
    metrics = None
    trace = None
    MeterProvider = None
    Resource = None
    TracerProvider = None
    OTEL_AVAILABLE = False


@dataclass(slots=True)
class MetricEvent:
    name: str
    value: float
    kind: str
    attributes: dict[str, Any]


_job_id: ContextVar[str | None] = ContextVar("evaluator_job_id", default=None)
_run_id: ContextVar[str | None] = ContextVar("evaluator_run_id", default=None)
_stage: ContextVar[str | None] = ContextVar("evaluator_stage", default=None)
_metric_events: list[MetricEvent] = []
_configured = False
_service_name = "curriculum-tutor-evaluator"
_tracer = None
_meter = None
_stage_duration_histogram = None
_stage_counter = None
_stage_failure_counter = None


def configure_telemetry(service: str = "curriculum-tutor-evaluator") -> None:
    global _configured, _service_name, _tracer, _meter, _stage_duration_histogram, _stage_counter, _stage_failure_counter
    _service_name = service
    if _configured:
        return
    otel_enabled = _env_bool("CURRICULUM_TUTOR_OTEL_ENABLED", False)
    if otel_enabled and not OTEL_AVAILABLE:
        raise RuntimeError("OpenTelemetry is enabled for the evaluator but opentelemetry packages are not installed")
    if OTEL_AVAILABLE and otel_enabled:
        resource = Resource.create({"service.name": _service_name, "service.namespace": "eduground"})
        trace.set_tracer_provider(TracerProvider(resource=resource))
        metrics.set_meter_provider(MeterProvider(resource=resource))
        _tracer = trace.get_tracer(_service_name)
        _meter = metrics.get_meter(_service_name)
        _stage_duration_histogram = _meter.create_histogram(
            f"{_service_name}.stage.duration.ms",
            unit="ms",
            description="Stage execution duration in milliseconds",
        )
        _stage_counter = _meter.create_counter(
            f"{_service_name}.stage.executions",
            description="Stage execution count",
        )
        _stage_failure_counter = _meter.create_counter(
            f"{_service_name}.stage.failures",
            description="Stage failure count",
        )
    _configured = True


@contextmanager
def bind_context(*, job_id: str | None = None, run_id: str | None = None, stage: str | None = None) -> Iterator[None]:
    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []
    if job_id is not None:
        tokens.append((_job_id, _job_id.set(job_id)))
    if run_id is not None:
        tokens.append((_run_id, _run_id.set(run_id)))
    if stage is not None:
        tokens.append((_stage, _stage.set(stage)))
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


@contextmanager
def stage_span(stage_name: str, *, logger: logging.Logger | None = None, attributes: dict[str, Any] | None = None) -> Iterator[None]:
    configure_telemetry()
    attrs = {key: value for key, value in (attributes or {}).items() if value is not None}
    attrs.setdefault("job_id", _job_id.get())
    attrs.setdefault("run_id", _run_id.get())
    attrs["stage"] = stage_name
    span_context = _tracer.start_as_current_span(f"{_service_name}.{stage_name}", attributes=attrs) if _tracer else nullcontext()
    with bind_context(stage=stage_name):
        started = perf_counter()
        if logger is not None:
            logger.info("Stage started", extra={"extra_json": {"stage": stage_name, **attrs}})
        try:
            with span_context:
                yield
        except Exception as exc:
            duration_ms = round((perf_counter() - started) * 1000, 3)
            failure_attrs = {**attrs, "status": "error", "error_type": exc.__class__.__name__}
            _record_metric("stage.duration.ms", duration_ms, "histogram", failure_attrs)
            _record_metric("stage.failures", 1.0, "counter", failure_attrs)
            if logger is not None:
                logger.exception(
                    "Stage failed",
                    extra={"extra_json": {"stage": stage_name, "duration_ms": duration_ms, "error_type": exc.__class__.__name__, **attrs}},
                )
            raise
        else:
            duration_ms = round((perf_counter() - started) * 1000, 3)
            success_attrs = {**attrs, "status": "ok"}
            _record_metric("stage.duration.ms", duration_ms, "histogram", success_attrs)
            _record_metric("stage.executions", 1.0, "counter", success_attrs)
            if logger is not None:
                logger.info("Stage completed", extra={"extra_json": {"stage": stage_name, "duration_ms": duration_ms, **attrs}})


def get_log_context() -> dict[str, Any]:
    payload = {
        "job_id": _job_id.get(),
        "run_id": _run_id.get(),
        "stage": _stage.get(),
    }
    if OTEL_AVAILABLE and trace is not None:
        span = trace.get_current_span()
        span_context = span.get_span_context() if span is not None else None
        if span_context and getattr(span_context, "is_valid", False):
            payload["trace_id"] = format(span_context.trace_id, "032x")
            payload["span_id"] = format(span_context.span_id, "016x")
    return {key: value for key, value in payload.items() if value is not None}


def get_metric_events() -> list[MetricEvent]:
    return list(_metric_events)


def reset_test_state() -> None:
    _metric_events.clear()


def _record_metric(name: str, value: float, kind: str, attributes: dict[str, Any]) -> None:
    clean_attributes = {key: value for key, value in attributes.items() if value is not None}
    _metric_events.append(MetricEvent(name=name, value=value, kind=kind, attributes=clean_attributes))
    if kind == "histogram" and _stage_duration_histogram is not None:
        _stage_duration_histogram.record(value, attributes=clean_attributes)
    elif kind == "counter":
        if name == "stage.failures" and _stage_failure_counter is not None:
            _stage_failure_counter.add(value, attributes=clean_attributes)
        elif _stage_counter is not None:
            _stage_counter.add(value, attributes=clean_attributes)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
