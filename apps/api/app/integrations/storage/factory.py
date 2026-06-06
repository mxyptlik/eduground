from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from app.core.config import settings
from app.core.errors import DependencyUnavailableError, ProviderRequestError
from app.core.logging import get_logger
from app.integrations.storage.base import ObjectStorage, StoredObjectMetadata
from app.integrations.storage.local_adapter import LocalDirectoryObjectStorage
from app.integrations.storage.s3_adapter import S3ObjectStorage

logger = get_logger("app.storage")

PRIMARY_STORAGE_BACKENDS = {"s3", "r2"}
SUPPORTED_STORAGE_BACKENDS = {"auto", "local", *PRIMARY_STORAGE_BACKENDS}


@dataclass(slots=True)
class ResilientObjectStorage(ObjectStorage):
    primary: S3ObjectStorage
    fallback: LocalDirectoryObjectStorage
    _primary_health_cache_until: float = field(default=0.0, init=False, repr=False)
    _primary_failure_backoff_until: float = field(default=0.0, init=False, repr=False)

    def create_signed_upload_url(self, storage_key: str, mime_type: str) -> str:
        if self._is_primary_available():
            try:
                return self.primary.create_signed_upload_url(storage_key, mime_type)
            except Exception as exc:
                self._mark_primary_unhealthy()
                logger.warning("Primary object storage upload URL generation failed, falling back to local storage", extra={"extra_json": {"error": str(exc)}})
        return self.fallback.create_signed_upload_url(storage_key, mime_type)

    def create_signed_download_url(self, storage_key: str) -> str:
        if self._is_primary_available():
            try:
                return self.primary.create_signed_download_url(storage_key)
            except Exception as exc:
                self._mark_primary_unhealthy()
                logger.warning("Primary object storage download URL generation failed, falling back to local storage", extra={"extra_json": {"error": str(exc)}})
        return self.fallback.create_signed_download_url(storage_key)

    def head_object(self, storage_key: str) -> StoredObjectMetadata | None:
        if self._is_primary_available():
            try:
                metadata = self.primary.head_object(storage_key)
                if metadata is not None:
                    return metadata
            except Exception as exc:
                self._mark_primary_unhealthy()
                logger.warning("Primary object storage HEAD failed, checking local fallback", extra={"extra_json": {"error": str(exc)}})
        return self.fallback.head_object(storage_key)

    def get_object_bytes(self, storage_key: str) -> bytes:
        if self._is_primary_available():
            try:
                payload = self.primary.get_object_bytes(storage_key)
                try:
                    self.fallback.put_object_bytes(storage_key, payload)
                except Exception as mirror_exc:
                    logger.warning("Could not mirror primary object payload to local fallback", extra={"extra_json": {"error": str(mirror_exc)}})
                return payload
            except Exception as exc:
                self._mark_primary_unhealthy()
                logger.warning("Primary object storage read failed, attempting local fallback", extra={"extra_json": {"error": str(exc)}})
        return self.fallback.get_object_bytes(storage_key)

    def is_primary_healthy(self) -> bool:
        try:
            self.primary.assert_bucket_available()
            self._mark_primary_healthy()
            return True
        except Exception:
            self._mark_primary_unhealthy()
            return False

    def is_fallback_healthy(self) -> bool:
        try:
            self.fallback.assert_storage_available()
            return True
        except Exception:
            return False

    def _is_primary_available(self) -> bool:
        now = monotonic()
        if now < self._primary_failure_backoff_until:
            return False
        if now < self._primary_health_cache_until:
            return True
        return self.is_primary_healthy()

    def _mark_primary_healthy(self) -> None:
        self._primary_health_cache_until = monotonic() + max(settings.object_storage_primary_health_ttl_seconds, 0.0)
        self._primary_failure_backoff_until = 0.0

    def _mark_primary_unhealthy(self) -> None:
        self._primary_health_cache_until = 0.0
        self._primary_failure_backoff_until = monotonic() + max(settings.object_storage_primary_failure_backoff_seconds, 0.0)


def _create_primary_storage() -> S3ObjectStorage:
    return S3ObjectStorage()


def _create_local_storage() -> LocalDirectoryObjectStorage:
    return LocalDirectoryObjectStorage()


def build_object_storage() -> ObjectStorage:
    backend = settings.object_storage_backend.strip().lower()
    if backend not in SUPPORTED_STORAGE_BACKENDS:
        raise DependencyUnavailableError(
            code="invalid_storage_backend",
            message=f"Unsupported CURRICULUM_TUTOR_OBJECT_STORAGE_BACKEND value '{settings.object_storage_backend}'",
            provider="storage",
        )

    if backend == "local":
        return _create_local_storage()

    if backend == "auto" and not settings.object_storage_local_fallback_enabled:
        return _create_primary_storage()

    if settings.object_storage_local_fallback_enabled:
        local = _create_local_storage()
        try:
            primary = _create_primary_storage()
            return ResilientObjectStorage(primary=primary, fallback=local)
        except Exception as exc:
            logger.warning("Primary object storage could not be initialized, using local fallback storage", extra={"extra_json": {"error": str(exc)}})
            return local

    return _create_primary_storage()


def get_object_storage() -> ObjectStorage:
    return build_object_storage()


def get_local_storage_for_signed_routes() -> LocalDirectoryObjectStorage:
    storage = get_object_storage()
    if isinstance(storage, LocalDirectoryObjectStorage):
        return storage
    if isinstance(storage, ResilientObjectStorage):
        return storage.fallback
    raise ProviderRequestError(
        code="local_storage_route_disabled",
        message="Local storage upload/download routes are not enabled for this deployment",
        status_code=404,
        retryable=False,
        provider="local_storage",
    )
