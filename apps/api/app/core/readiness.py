from __future__ import annotations

from dataclasses import dataclass
from socket import create_connection
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger
from app.core.observability import gauge, traced_operation
from app.db.session import engine
from app.integrations.storage.factory import ResilientObjectStorage, get_object_storage
from app.integrations.storage.local_adapter import LocalDirectoryObjectStorage

logger = get_logger("app.readiness")


@dataclass(slots=True)
class DependencyCheckResult:
    name: str
    healthy: bool
    latency_ms: int
    message: str
    details: dict[str, Any]


def _result(name: str, started: float, healthy: bool, message: str, **details: Any) -> DependencyCheckResult:
    latency_ms = int((perf_counter() - started) * 1000)
    gauge(
        "eduground_dependency_health",
        1 if healthy else 0,
        labels={"dependency": name},
        description="Dependency health status where 1 is healthy and 0 is unhealthy",
    )
    return DependencyCheckResult(
        name=name,
        healthy=healthy,
        latency_ms=latency_ms,
        message=message,
        details=details,
    )


def _socket_check(name: str, target_url: str) -> DependencyCheckResult:
    started = perf_counter()
    parsed = urlparse(target_url)
    host = parsed.hostname or "localhost"
    default_port = 443 if parsed.scheme.endswith("s") else 80
    port = parsed.port or default_port
    try:
        with create_connection((host, port), timeout=5):
            return _result(name, started, True, "Socket connection succeeded", host=host, port=port)
    except OSError as exc:
        return _result(name, started, False, f"Socket connection failed: {exc}", host=host, port=port)


def check_database() -> DependencyCheckResult:
    started = perf_counter()
    try:
        with traced_operation("readiness.database", metric_name="eduground_dependency_check", metric_labels={"dependency": "postgres"}):
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        return _result("postgres", started, True, "Database responded to SELECT 1")
    except Exception as exc:
        return _result("postgres", started, False, f"Database check failed: {exc}")


def check_redis() -> DependencyCheckResult:
    return _socket_check("redis", settings.redis_url)


def check_qdrant() -> DependencyCheckResult:
    started = perf_counter()
    headers = {"Accept": "application/json"}
    if settings.qdrant_api_key:
        headers["Api-Key"] = settings.qdrant_api_key
    try:
        with traced_operation("readiness.qdrant", metric_name="eduground_dependency_check", metric_labels={"dependency": "qdrant"}):
            response = httpx.get(
                f"{settings.qdrant_url.rstrip('/')}/collections/{settings.qdrant_collection_name}",
                headers=headers,
                timeout=5.0,
            )
        if response.status_code >= 400:
            return _result("qdrant", started, False, f"Qdrant returned {response.status_code}", status_code=response.status_code)
        return _result("qdrant", started, True, "Qdrant collection is reachable", status_code=response.status_code)
    except Exception as exc:
        return _result("qdrant", started, False, f"Qdrant check failed: {exc}")


def check_openrouter() -> DependencyCheckResult:
    started = perf_counter()
    headers = {"Accept": "application/json"}
    if settings.openrouter_api_key:
        headers["Authorization"] = f"Bearer {settings.openrouter_api_key}"
    try:
        with traced_operation("readiness.openrouter", metric_name="eduground_dependency_check", metric_labels={"dependency": "openrouter"}):
            response = httpx.get(f"{settings.openrouter_base_url.rstrip('/')}/models", headers=headers, timeout=5.0)
        if response.status_code >= 400:
            return _result(
                "openrouter",
                started,
                False,
                f"OpenRouter returned {response.status_code}",
                status_code=response.status_code,
            )
        return _result("openrouter", started, True, "OpenRouter models endpoint is reachable", status_code=response.status_code)
    except Exception as exc:
        return _result("openrouter", started, False, f"OpenRouter check failed: {exc}")


def check_gemini() -> DependencyCheckResult:
    started = perf_counter()
    if not settings.gemini_fallback_enabled:
        return _result("gemini", started, True, "Gemini fallback is disabled", mode="disabled")
    if not settings.gemini_api_key:
        return _result("gemini", started, True, "Gemini fallback is enabled but no API key is configured", fallback_available=False)
    headers = {"Accept": "application/json", "x-goog-api-key": settings.gemini_api_key}
    try:
        with traced_operation("readiness.gemini", metric_name="eduground_dependency_check", metric_labels={"dependency": "gemini"}):
            response = httpx.get(f"{settings.gemini_base_url.rstrip('/')}/models", headers=headers, timeout=5.0)
        if response.status_code >= 400:
            return _result("gemini", started, False, f"Gemini returned {response.status_code}", status_code=response.status_code)
        return _result("gemini", started, True, "Gemini models endpoint is reachable", status_code=response.status_code)
    except Exception as exc:
        return _result("gemini", started, False, f"Gemini check failed: {exc}")


def check_clerk() -> DependencyCheckResult:
    started = perf_counter()
    if not settings.clerk_enabled:
        return _result("clerk", started, True, "Clerk auth is disabled for this deployment", mode="disabled")
    try:
        with traced_operation("readiness.clerk", metric_name="eduground_dependency_check", metric_labels={"dependency": "clerk"}):
            response = httpx.get(settings.clerk_jwks_url, timeout=5.0)
        if response.status_code >= 400:
            return _result("clerk", started, False, f"Clerk returned {response.status_code}", status_code=response.status_code)
        return _result("clerk", started, True, "Clerk JWKS endpoint is reachable", status_code=response.status_code)
    except Exception as exc:
        return _result("clerk", started, False, f"Clerk check failed: {exc}")


def check_r2() -> DependencyCheckResult:
    started = perf_counter()
    try:
        with traced_operation("readiness.r2", metric_name="eduground_dependency_check", metric_labels={"dependency": "r2"}):
            storage = get_object_storage()
            if isinstance(storage, ResilientObjectStorage):
                primary_healthy = storage.is_primary_healthy()
                fallback_healthy = storage.is_fallback_healthy()
                if primary_healthy:
                    return _result(
                        "r2",
                        started,
                        True,
                        "Primary object storage bucket is reachable",
                        bucket=settings.s3_bucket,
                        backend="r2",
                        fallback_healthy=fallback_healthy,
                    )
                if fallback_healthy:
                    return _result(
                        "r2",
                        started,
                        True,
                        "Primary object storage is unavailable; local fallback is active",
                        bucket=settings.s3_bucket,
                        backend="local_fallback",
                        fallback_healthy=True,
                    )
                return _result(
                    "r2",
                    started,
                    False,
                    "Primary object storage is unavailable and local fallback is unhealthy",
                    bucket=settings.s3_bucket,
                    backend="degraded",
                    fallback_healthy=False,
                )
            if isinstance(storage, LocalDirectoryObjectStorage):
                storage.assert_storage_available()
                return _result("r2", started, True, "Local object storage backend is active", backend="local")

            assert_bucket_available = getattr(storage, "assert_bucket_available", None)
            if callable(assert_bucket_available):
                assert_bucket_available()
            return _result("r2", started, True, "Object storage backend is reachable", bucket=settings.s3_bucket, backend="r2")
    except Exception as exc:
        return _result("r2", started, False, f"Object storage check failed: {exc}", bucket=settings.s3_bucket)


def check_unstructured() -> DependencyCheckResult:
    started = perf_counter()
    headers = {"Accept": "application/json"}
    if settings.unstructured_api_key:
        headers["unstructured-api-key"] = settings.unstructured_api_key
    try:
        with traced_operation("readiness.unstructured", metric_name="eduground_dependency_check", metric_labels={"dependency": "unstructured"}):
            response = httpx.options(settings.unstructured_api_url, headers=headers, timeout=5.0)
        healthy = response.status_code < 500
        message = "Unstructured endpoint responded" if healthy else f"Unstructured returned {response.status_code}"
        return _result("unstructured", started, healthy, message, status_code=response.status_code)
    except Exception as exc:
        return _result("unstructured", started, False, f"Unstructured check failed: {exc}")


def collect_dependency_checks() -> list[DependencyCheckResult]:
    results = [
        check_database(),
        check_redis(),
        check_qdrant(),
        check_openrouter(),
        check_gemini(),
        check_clerk(),
        check_r2(),
        check_unstructured(),
    ]
    logger.info(
        "Completed readiness dependency checks",
        extra={
            "extra_json": {
                "dependencies": [
                    {"name": item.name, "healthy": item.healthy, "latency_ms": item.latency_ms}
                    for item in results
                ]
            }
        },
    )
    return results
