from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import logging
from threading import Lock
from time import perf_counter
from typing import Any

from app.core.config import settings
from app.core.runtime import RuntimeConfigurationError

logger = logging.getLogger("app.observability")


def _sanitize_metric_name(name: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in name)


def _normalize_labels(labels: dict[str, Any] | None = None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(key), str(value)) for key, value in labels.items() if value is not None))


def _format_label_set(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    rendered = ",".join(f'{key}="{value}"' for key, value in labels)
    return "{" + rendered + "}"


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._metric_types: dict[str, str] = {}
        self._metric_help: dict[str, str] = {}
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(lambda: defaultdict(float))
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(dict)
        self._histogram_sum: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(lambda: defaultdict(float))
        self._histogram_count: dict[str, dict[tuple[tuple[str, str], ...], int]] = defaultdict(lambda: defaultdict(int))
        self._histogram_buckets: dict[str, dict[tuple[tuple[str, str], ...], dict[float, int]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        self._default_buckets = (5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0)

    def counter(self, name: str, value: float = 1.0, *, labels: dict[str, Any] | None = None, description: str = "") -> None:
        metric_name = _sanitize_metric_name(name)
        label_set = _normalize_labels(labels)
        with self._lock:
            self._metric_types[metric_name] = "counter"
            if description:
                self._metric_help[metric_name] = description
            self._counters[metric_name][label_set] += value

    def gauge(self, name: str, value: float, *, labels: dict[str, Any] | None = None, description: str = "") -> None:
        metric_name = _sanitize_metric_name(name)
        label_set = _normalize_labels(labels)
        with self._lock:
            self._metric_types[metric_name] = "gauge"
            if description:
                self._metric_help[metric_name] = description
            self._gauges[metric_name][label_set] = value

    def histogram(
        self,
        name: str,
        value: float,
        *,
        labels: dict[str, Any] | None = None,
        description: str = "",
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        metric_name = _sanitize_metric_name(name)
        label_set = _normalize_labels(labels)
        bucket_values = buckets or self._default_buckets
        with self._lock:
            self._metric_types[metric_name] = "histogram"
            if description:
                self._metric_help[metric_name] = description
            self._histogram_sum[metric_name][label_set] += float(value)
            self._histogram_count[metric_name][label_set] += 1
            bucket_store = self._histogram_buckets[metric_name][label_set]
            for bucket in bucket_values:
                bucket_store.setdefault(bucket, 0)
                if value <= bucket:
                    bucket_store[bucket] += 1
            bucket_store.setdefault(float("inf"), 0)
            bucket_store[float("inf")] += 1

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for metric_name in sorted(self._metric_types):
                metric_type = self._metric_types[metric_name]
                help_text = self._metric_help.get(metric_name, metric_name.replace("_", " "))
                lines.append(f"# HELP {metric_name} {help_text}")
                lines.append(f"# TYPE {metric_name} {metric_type}")
                if metric_type == "counter":
                    for labels, value in sorted(self._counters[metric_name].items()):
                        lines.append(f"{metric_name}{_format_label_set(labels)} {value}")
                elif metric_type == "gauge":
                    for labels, value in sorted(self._gauges[metric_name].items()):
                        lines.append(f"{metric_name}{_format_label_set(labels)} {value}")
                elif metric_type == "histogram":
                    for labels, bucket_map in sorted(self._histogram_buckets[metric_name].items()):
                        for bucket, count in sorted(bucket_map.items(), key=lambda item: item[0]):
                            bucket_label_value = "+Inf" if bucket == float("inf") else str(bucket)
                            lines.append(
                                f'{metric_name}_bucket{_format_label_set(labels + (("le", bucket_label_value),))} {count}'
                            )
                        lines.append(
                            f"{metric_name}_sum{_format_label_set(labels)} {self._histogram_sum[metric_name][labels]}"
                        )
                        lines.append(
                            f"{metric_name}_count{_format_label_set(labels)} {self._histogram_count[metric_name][labels]}"
                        )
        return "\n".join(lines) + "\n"


metrics_registry = MetricsRegistry()


@dataclass(slots=True)
class ObservabilityState:
    service: str = "api"
    configured: bool = False
    otel_enabled: bool = False
    tracer_provider: Any | None = None


_state = ObservabilityState()


def _parse_otlp_headers(raw_value: str) -> dict[str, str]:
    if not raw_value.strip():
        return {}
    headers: dict[str, str] = {}
    for part in raw_value.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", maxsplit=1)
        headers[key.strip()] = value.strip()
    return headers


def configure_observability(*, service: str, fastapi_app: Any | None = None) -> None:
    if _state.configured and _state.service == service:
        return

    _state.service = service
    metrics_registry.gauge(
        "eduground_service_info",
        1,
        labels={"service": service, "environment": settings.app_env},
        description="Static service metadata for the active process",
    )

    if not settings.otel_enabled:
        _state.configured = True
        _state.otel_enabled = False
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    except ImportError as exc:  # pragma: no cover - depends on installed extras
        raise RuntimeConfigurationError(
            "OpenTelemetry is enabled but the required opentelemetry packages are not installed"
        ) from exc

    resource = Resource.create(
        {
            "service.name": service,
            "service.namespace": "eduground",
            "deployment.environment": settings.app_env,
        }
    )
    provider = TracerProvider(
        resource=resource,
        sampler=TraceIdRatioBased(settings.otel_trace_sample_ratio),
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=settings.otel_exporter_otlp_endpoint,
                headers=_parse_otlp_headers(settings.otel_exporter_otlp_headers),
                timeout=settings.otel_exporter_timeout_seconds,
            )
        )
    )
    trace.set_tracer_provider(provider)

    if service == "api" and fastapi_app is not None:
        FastAPIInstrumentor.instrument_app(fastapi_app, tracer_provider=provider)
        HTTPXClientInstrumentor().instrument()
        try:
            from app.db.session import engine

            SQLAlchemyInstrumentor().instrument(engine=engine)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to instrument SQLAlchemy", extra={"extra_json": {"error": str(exc)}})
    elif service in {"worker", "evaluator"}:
        HTTPXClientInstrumentor().instrument()

    _state.configured = True
    _state.otel_enabled = True
    _state.tracer_provider = provider


def current_trace_context() -> dict[str, str]:
    if not _state.otel_enabled:
        return {}
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover - defensive
        return {}
    span = trace.get_current_span()
    context = span.get_span_context()
    if not context or not context.is_valid:
        return {}
    return {
        "trace_id": f"{context.trace_id:032x}",
        "span_id": f"{context.span_id:016x}",
    }


@contextmanager
def traced_operation(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
    metric_name: str | None = None,
    metric_labels: dict[str, Any] | None = None,
) -> Iterator[Any]:
    started = perf_counter()
    status_label = "success"
    error_type: str | None = None
    span = None
    tracer = None
    if _state.otel_enabled:
        try:
            from opentelemetry import trace
            from opentelemetry.trace import Status, StatusCode

            tracer = trace.get_tracer(f"eduground.{_state.service}")
            span = tracer.start_span(name)
            span.__enter__()
            if attributes:
                for key, value in attributes.items():
                    span.set_attribute(key, value)
        except Exception:  # pragma: no cover - defensive
            span = None
            tracer = None
    try:
        yield span
    except Exception as exc:
        status_label = "error"
        error_type = exc.__class__.__name__
        if span is not None:
            from opentelemetry.trace import Status, StatusCode

            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
        raise
    else:
        if span is not None:
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.OK))
    finally:
        duration_ms = (perf_counter() - started) * 1000
        if metric_name:
            labels = dict(metric_labels or {})
            labels["status"] = status_label
            if error_type:
                labels["error_type"] = error_type
            counter(
                f"{metric_name}_total",
                labels=labels,
                description=f"Total executions for {metric_name}",
            )
            histogram(
                f"{metric_name}_duration_ms",
                duration_ms,
                labels=labels,
                description=f"Execution duration for {metric_name} in milliseconds",
            )
        if span is not None:
            span.__exit__(None, None, None)


def counter(name: str, *, labels: dict[str, Any] | None = None, amount: float = 1.0, description: str = "") -> None:
    metrics_registry.counter(name, amount, labels=labels, description=description)


def gauge(name: str, value: float, *, labels: dict[str, Any] | None = None, description: str = "") -> None:
    metrics_registry.gauge(name, value, labels=labels, description=description)


def histogram(name: str, value: float, *, labels: dict[str, Any] | None = None, description: str = "") -> None:
    metrics_registry.histogram(name, value, labels=labels, description=description)
