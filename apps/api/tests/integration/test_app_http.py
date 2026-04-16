from __future__ import annotations

import pytest

from app import main as app_main
from app.db.session import get_db_session

pytestmark = pytest.mark.integration


def test_healthcheck_reports_environment_metadata(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "environment" in response.json()
    assert "app_name" in response.json()
    assert response.json()["request_id"] is not None


def test_security_headers_are_present_on_healthcheck(client) -> None:
    response = client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"


def test_request_context_middleware_sets_request_id_header(client) -> None:
    response = client.get("/health", headers={"X-Request-Id": "req-integration-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "req-integration-123"


def test_request_size_limit_rejects_large_webhook_payload(client, set_setting, override_dependency) -> None:
    set_setting("max_api_request_bytes", 16)
    override_dependency(get_db_session, lambda: None)

    response = client.post(
        "/api/webhooks/clerk",
        content=b"x" * 64,
        headers={"content-length": "64", "content-type": "application/json"},
    )

    assert response.status_code == 413
    assert "Request body too large" in response.json()["detail"]
    assert response.json()["code"] == "payload_too_large"


def test_metrics_endpoint_exposes_prometheus_text(client) -> None:
    client.get("/health")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "eduground_http_requests_total" in response.text
    assert "eduground_service_info" in response.text


def test_readiness_reports_degraded_dependencies(client, monkeypatch) -> None:
    monkeypatch.setattr(
        app_main,
        "collect_dependency_checks",
        lambda: [
            app_main.DependencyStatusResponse(
                name="postgres",
                healthy=True,
                latency_ms=5,
                message="ok",
                details={},
            ).model_copy(),
        ],
    )

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
