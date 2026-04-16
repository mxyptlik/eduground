from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Keep tests independent from external Postgres/psycopg availability.
os.environ.setdefault("CURRICULUM_TUTOR_DATABASE_URL", "sqlite:///./curriculum_tutor_test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///./curriculum_tutor_test.db")

from app.api.deps import RequestAuthContext
from app.core.config import settings
from app.models.enums import InstitutionRole
from app.schemas.auth import AuthUser, OrganizationMembershipSummary


@pytest.fixture
def app_module(monkeypatch):
    from app import main as app_main

    monkeypatch.setattr(app_main, "validate_runtime_configuration", lambda: None)
    monkeypatch.setattr(app_main, "initialize_database", lambda: None)
    app_main.app.dependency_overrides.clear()
    return app_main


@pytest.fixture
def app(app_module) -> FastAPI:
    return app_module.app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def set_setting(monkeypatch) -> Callable[[str, Any], None]:
    def _set(name: str, value: Any) -> None:
        monkeypatch.setattr(settings, name, value)

    return _set


@pytest.fixture
def override_dependency(app: FastAPI) -> Iterator[Callable[[Callable[..., Any], Callable[..., Any]], None]]:
    registered: list[Callable[..., Any]] = []

    def _override(target: Callable[..., Any], replacement: Callable[..., Any]) -> None:
        app.dependency_overrides[target] = replacement
        registered.append(target)

    yield _override

    for target in registered:
        app.dependency_overrides.pop(target, None)


@pytest.fixture
def auth_user_payload() -> AuthUser:
    return AuthUser(
        id="9d201051-8e77-4320-bd90-ec7ce4f0527d",
        email="instructor@example.edu",
        display_name="Test Instructor",
        institution_id="b4880f64-59ae-4310-9c3c-7a1202344297",
        role=InstitutionRole.INSTRUCTOR,
        external_subject_id="user_123",
        active_organization_id="org_123",
        active_organization_slug="example-university",
        memberships=[
            OrganizationMembershipSummary(
                institution_id="b4880f64-59ae-4310-9c3c-7a1202344297",
                institution_name="Example University",
                institution_slug="example-university",
                external_organization_id="org_123",
                role=InstitutionRole.INSTRUCTOR,
            )
        ],
    )


@pytest.fixture
def auth_context(auth_user_payload: AuthUser) -> RequestAuthContext:
    membership = SimpleNamespace(
        institution_id=auth_user_payload.institution_id,
        role=auth_user_payload.role,
    )
    user = SimpleNamespace(
        id=auth_user_payload.id,
        institution_id=auth_user_payload.institution_id,
        email=auth_user_payload.email,
        display_name=auth_user_payload.display_name,
        external_subject_id=auth_user_payload.external_subject_id,
    )
    session = SimpleNamespace(
        subject_id=auth_user_payload.external_subject_id,
        active_organization_id=auth_user_payload.active_organization_id,
        organization_role="org:admin",
        organization_slug=auth_user_payload.active_organization_slug,
        claims={"sub": auth_user_payload.external_subject_id, "org_id": auth_user_payload.active_organization_id},
    )
    return RequestAuthContext(
        session=session,
        user=user,
        membership=membership,
        active_organization_id=auth_user_payload.active_organization_id,
        auth_user=auth_user_payload,
    )


@pytest.fixture
def clerk_session():
    return SimpleNamespace(
        subject_id="user_123",
        active_organization_id="org_123",
        organization_role="org:admin",
        organization_slug="example-university",
        claims={"sub": "user_123", "org_id": "org_123"},
    )


@pytest.fixture
def svix_secret() -> str:
    return "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode("utf-8")


@pytest.fixture
def sign_svix_payload() -> Callable[[str, dict[str, Any], str | None], tuple[bytes, dict[str, str]]]:
    def _sign(secret: str, event: dict[str, Any], *, msg_id: str | None = None) -> tuple[bytes, dict[str, str]]:
        payload = json.dumps(event).encode("utf-8")
        timestamp = str(int(time.time()))
        signed_payload = b".".join(
            [
                (msg_id or "msg_test_123").encode("utf-8"),
                timestamp.encode("utf-8"),
                payload,
            ]
        )
        signing_key = base64.b64decode(secret.removeprefix("whsec_"))
        signature = base64.b64encode(hmac.new(signing_key, signed_payload, hashlib.sha256).digest()).decode("utf-8")
        headers = {
            "svix-id": msg_id or "msg_test_123",
            "svix-timestamp": timestamp,
            "svix-signature": f"v1,{signature}",
        }
        return payload, headers

    return _sign
