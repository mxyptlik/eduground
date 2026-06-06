from __future__ import annotations

import pytest

from app.services import storage


def test_s3_backend_reads_from_local_fallback_when_primary_fails(monkeypatch) -> None:
    monkeypatch.setattr(storage.settings, "object_storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "object_storage_local_fallback_enabled", True)
    monkeypatch.setattr(storage, "_read_s3", lambda storage_key: (_ for _ in ()).throw(storage.StorageError("s3 failed")))
    monkeypatch.setattr(storage, "_read_local", lambda storage_key: b"fallback-bytes")

    assert storage.get_object_bytes("uploads/notebook/source.pdf") == b"fallback-bytes"


def test_s3_backend_raises_primary_failure_when_fallback_disabled(monkeypatch) -> None:
    monkeypatch.setattr(storage.settings, "object_storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "object_storage_local_fallback_enabled", False)
    monkeypatch.setattr(storage, "_read_s3", lambda storage_key: (_ for _ in ()).throw(storage.StorageError("s3 failed")))
    monkeypatch.setattr(storage, "_read_local", lambda storage_key: b"fallback-bytes")

    with pytest.raises(storage.StorageError, match="s3 failed"):
        storage.get_object_bytes("uploads/notebook/source.pdf")
