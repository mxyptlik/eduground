from __future__ import annotations

import pytest

from app.api.deps import get_auth_context
from app.api.routes import auth as auth_routes
from app.core.security import WebhookVerificationError
from app.db.session import get_db_session

pytestmark = pytest.mark.module


def test_me_returns_auth_user(client, override_dependency, auth_context) -> None:
    override_dependency(get_auth_context, lambda: auth_context)

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "instructor@example.edu"
    assert body["active_organization_id"] == "org_123"
    assert body["memberships"][0]["external_organization_id"] == "org_123"


def test_clerk_webhook_accepts_verified_event(client, monkeypatch, override_dependency) -> None:
    seen: dict[str, object] = {}

    override_dependency(get_db_session, lambda: None)
    monkeypatch.setattr(auth_routes, "verify_svix_webhook", lambda payload, headers: None)

    class FakeAuthService:
        def __init__(self, db) -> None:
            self.db = db

        def handle_clerk_webhook(self, event_type: str, payload: dict) -> None:
            seen["event_type"] = event_type
            seen["payload"] = payload

    monkeypatch.setattr(auth_routes, "AuthService", FakeAuthService)

    response = client.post(
        "/api/webhooks/clerk",
        json={"type": "user.created", "data": {"id": "user_123"}},
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "processed_event_type": "user.created"}
    assert seen["event_type"] == "user.created"


def test_clerk_webhook_returns_401_when_verification_fails(client, monkeypatch, override_dependency) -> None:
    override_dependency(get_db_session, lambda: None)

    def _fail(payload, headers) -> None:
        raise WebhookVerificationError("signature mismatch")

    monkeypatch.setattr(auth_routes, "verify_svix_webhook", _fail)

    response = client.post(
        "/api/webhooks/clerk",
        json={"type": "user.created", "data": {"id": "user_123"}},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "signature mismatch"
    assert response.json()["code"] == "unauthorized"
