from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api import deps as auth_deps
from app.api.routes import auth as auth_routes
from app.db.session import get_db_session

pytestmark = pytest.mark.system


def test_auth_me_returns_503_when_clerk_is_disabled(client, set_setting, override_dependency) -> None:
    set_setting("clerk_enabled", False)
    override_dependency(get_db_session, lambda: None)

    response = client.get("/api/auth/me")

    assert response.status_code == 503
    assert response.json()["detail"] == "Clerk authentication is not enabled for this deployment"
    assert response.json()["code"] == "clerk_not_configured"
    assert response.json()["provider"] == "clerk"


def test_auth_me_returns_401_when_token_is_missing(client, set_setting, override_dependency) -> None:
    set_setting("clerk_enabled", True)
    override_dependency(get_db_session, lambda: None)

    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Clerk bearer token"
    assert response.json()["code"] == "missing_bearer_token"


def test_auth_me_uses_real_dependency_path_with_bearer_token(
    client,
    monkeypatch,
    set_setting,
    override_dependency,
    auth_user_payload,
    clerk_session,
) -> None:
    set_setting("clerk_enabled", True)
    override_dependency(get_db_session, lambda: None)

    class FakeVerifier:
        def verify(self, token: str):
            assert token == "token-123"
            return clerk_session

    class FakeAuthService:
        def __init__(self, db) -> None:
            self.db = db

        def sync_clerk_session(self, session, *, active_organization_id=None):
            user = SimpleNamespace(
                id=auth_user_payload.id,
                institution_id=auth_user_payload.institution_id,
                email=auth_user_payload.email,
                display_name=auth_user_payload.display_name,
                external_subject_id=auth_user_payload.external_subject_id,
            )
            membership = SimpleNamespace(
                institution_id=auth_user_payload.institution_id,
                role=auth_user_payload.role,
            )
            assert session.subject_id == clerk_session.subject_id
            assert active_organization_id == "org_override"
            return user, membership

        def build_auth_user(self, *, user, active_membership, active_organization_id):
            return auth_user_payload.model_copy(update={"active_organization_id": active_organization_id})

    monkeypatch.setattr(auth_deps, "get_clerk_token_verifier", lambda: FakeVerifier())
    monkeypatch.setattr(auth_deps, "AuthService", FakeAuthService)

    response = client.get(
        "/api/auth/me",
        headers={
            "Authorization": "Bearer token-123",
            "X-Active-Organization-Id": "org_override",
        },
    )

    assert response.status_code == 200
    assert response.json()["active_organization_id"] == "org_override"
    assert response.json()["external_subject_id"] == "user_123"


def test_clerk_webhook_accepts_real_svix_signature(
    client,
    monkeypatch,
    set_setting,
    override_dependency,
    svix_secret,
    sign_svix_payload,
) -> None:
    set_setting("clerk_webhook_signing_secret", svix_secret)
    override_dependency(get_db_session, lambda: None)

    processed: dict[str, object] = {}

    class FakeAuthService:
        def __init__(self, db) -> None:
            self.db = db

        def handle_clerk_webhook(self, event_type: str, payload: dict) -> None:
            processed["event_type"] = event_type
            processed["payload"] = payload

    monkeypatch.setattr(auth_routes, "AuthService", FakeAuthService)

    payload, headers = sign_svix_payload(
        svix_secret,
        {"type": "organization.updated", "data": {"id": "org_123", "name": "Example University"}},
    )
    response = client.post("/api/webhooks/clerk", content=payload, headers=headers)

    assert response.status_code == 202
    assert processed["event_type"] == "organization.updated"
