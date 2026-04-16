from __future__ import annotations

import pytest

from app.integrations.storage.factory import ResilientObjectStorage


pytestmark = pytest.mark.unit


class _FakePrimaryStorage:
    def __init__(self, *, healthy: bool) -> None:
        self.healthy = healthy
        self.health_checks = 0
        self.upload_calls = 0

    def assert_bucket_available(self) -> None:
        self.health_checks += 1
        if not self.healthy:
            raise RuntimeError("primary unavailable")

    def create_signed_upload_url(self, storage_key: str, mime_type: str) -> str:
        self.upload_calls += 1
        return f"https://primary.example/{storage_key}"

    def create_signed_download_url(self, storage_key: str) -> str:
        return f"https://primary.example/download/{storage_key}"

    def head_object(self, storage_key: str):
        return None

    def get_object_bytes(self, storage_key: str) -> bytes:
        return b"primary"


class _FakeFallbackStorage:
    def __init__(self) -> None:
        self.upload_calls = 0

    def create_signed_upload_url(self, storage_key: str, mime_type: str) -> str:
        self.upload_calls += 1
        return f"http://localhost/fallback/{storage_key}"

    def create_signed_download_url(self, storage_key: str) -> str:
        return f"http://localhost/fallback/download/{storage_key}"

    def head_object(self, storage_key: str):
        return None

    def get_object_bytes(self, storage_key: str) -> bytes:
        return b"fallback"

    def put_object_bytes(self, storage_key: str, data: bytes, *, content_type: str | None = None) -> None:
        return None

    def assert_storage_available(self) -> None:
        return None


def test_resilient_storage_backs_off_after_primary_health_failure(set_setting) -> None:
    set_setting("object_storage_primary_failure_backoff_seconds", 300.0)
    set_setting("object_storage_primary_health_ttl_seconds", 60.0)
    primary = _FakePrimaryStorage(healthy=False)
    fallback = _FakeFallbackStorage()
    storage = ResilientObjectStorage(primary=primary, fallback=fallback)

    first_url = storage.create_signed_upload_url("alpha", "application/pdf")
    second_url = storage.create_signed_upload_url("beta", "application/pdf")

    assert first_url == "http://localhost/fallback/alpha"
    assert second_url == "http://localhost/fallback/beta"
    assert primary.health_checks == 1
    assert primary.upload_calls == 0
    assert fallback.upload_calls == 2


def test_resilient_storage_reuses_primary_health_cache(set_setting) -> None:
    set_setting("object_storage_primary_failure_backoff_seconds", 300.0)
    set_setting("object_storage_primary_health_ttl_seconds", 60.0)
    primary = _FakePrimaryStorage(healthy=True)
    fallback = _FakeFallbackStorage()
    storage = ResilientObjectStorage(primary=primary, fallback=fallback)

    first_url = storage.create_signed_upload_url("alpha", "application/pdf")
    second_url = storage.create_signed_upload_url("beta", "application/pdf")

    assert first_url == "https://primary.example/alpha"
    assert second_url == "https://primary.example/beta"
    assert primary.health_checks == 1
    assert primary.upload_calls == 2
    assert fallback.upload_calls == 0
