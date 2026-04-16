from __future__ import annotations

from types import SimpleNamespace

from app.services import auth as auth_service_module


def test_sync_clerk_session_uses_local_records_before_clerk_management(monkeypatch) -> None:
    class FakeClerkManagementClient:
        def get_user(self, user_id: str):
            raise AssertionError(f"unexpected Clerk user fetch for {user_id}")

        def get_organization(self, organization_id: str):
            raise AssertionError(f"unexpected Clerk organization fetch for {organization_id}")

    class FakeAuditService:
        def __init__(self, db) -> None:
            self.db = db

        def record(self, **kwargs) -> None:
            return None

    institution = SimpleNamespace(id="inst-1", slug="example-university")
    user = SimpleNamespace(
        id="user-local-1",
        institution_id=None,
        email="instructor@example.edu",
        display_name="Instructor Example",
        external_subject_id="user_123",
        status=None,
        last_login_at=None,
    )

    class FakeUserRepository:
        def __init__(self, db) -> None:
            self.db = db

        def get_institution_by_external_organization_id(self, external_organization_id: str):
            assert external_organization_id == "org_123"
            return institution

        def get_by_external_subject_id(self, external_subject_id: str, *, auth_provider=None):
            assert external_subject_id == "user_123"
            return user

        def get_membership_for_institution_user(self, institution_id: str, user_id: str):
            assert institution_id == "inst-1"
            assert user_id == "user-local-1"
            return None

        def upsert_clerk_membership(self, **kwargs):
            return SimpleNamespace(
                id="membership-1",
                institution_id=kwargs["institution_id"],
                user_id=kwargs["user_id"],
                role=kwargs["role"],
                external_membership_id=kwargs["external_membership_id"],
                membership_metadata_json=kwargs["membership_metadata_json"],
            )

    fake_db = SimpleNamespace(
        add=lambda *args, **kwargs: None,
        flush=lambda: None,
        commit=lambda: None,
        refresh=lambda obj: None,
    )
    session = SimpleNamespace(
        subject_id="user_123",
        active_organization_id="org_123",
        organization_role="org:admin",
        organization_slug="example-university",
        claims={"sub": "user_123", "org_id": "org_123", "org_membership_id": "mem_123"},
    )

    monkeypatch.setattr(
        auth_service_module,
        "get_clerk_management_client",
        lambda: FakeClerkManagementClient(),
    )
    monkeypatch.setattr(auth_service_module, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(auth_service_module, "AuditService", FakeAuditService)

    service = auth_service_module.AuthService(fake_db)

    synced_user, membership = service.sync_clerk_session(session)

    assert synced_user is user
    assert synced_user.institution_id == "inst-1"
    assert synced_user.last_login_at is not None
    assert membership is not None
    assert membership.institution_id == "inst-1"
    assert membership.external_membership_id == "mem_123"
