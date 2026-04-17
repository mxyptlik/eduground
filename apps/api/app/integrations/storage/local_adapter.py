from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.core.errors import ProviderRequestError
from app.integrations.storage.base import ObjectStorage, StoredObjectMetadata


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


@dataclass(slots=True)
class LocalSignedStorageRequest:
    storage_key: str
    mime_type: str | None


class LocalDirectoryObjectStorage(ObjectStorage):
    def __init__(self) -> None:
        self._root = Path(settings.object_storage_local_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._secret = settings.secret_key.encode("utf-8")
        self._api_base_url = settings.api_public_base_url.rstrip("/")

    def create_signed_upload_url(self, storage_key: str, mime_type: str) -> str:
        token = self._issue_token(storage_key=storage_key, operation="upload", mime_type=mime_type)
        return f"{self._api_base_url}{settings.api_prefix}/storage/local-upload?token={token}"

    def create_signed_download_url(self, storage_key: str) -> str:
        token = self._issue_token(storage_key=storage_key, operation="download")
        return f"{self._api_base_url}{settings.api_prefix}/storage/local-download?token={token}"

    def head_object(self, storage_key: str) -> StoredObjectMetadata | None:
        data_path = self._resolve_storage_path(storage_key)
        if not data_path.exists() or not data_path.is_file():
            return None
        byte_size = data_path.stat().st_size
        etag = hashlib.sha256(data_path.read_bytes()).hexdigest()
        content_type = self._read_content_type(data_path)
        return StoredObjectMetadata(
            storage_key=storage_key,
            byte_size=byte_size,
            etag=etag,
            content_type=content_type,
        )

    def get_object_bytes(self, storage_key: str) -> bytes:
        data_path = self._resolve_storage_path(storage_key)
        if not data_path.exists() or not data_path.is_file():
            raise ProviderRequestError(
                code="object_storage_read_failed",
                message=f"Object not found for storage key '{storage_key}'",
                status_code=404,
                retryable=False,
                provider="local_storage",
            )
        return data_path.read_bytes()

    def put_object_bytes(self, storage_key: str, data: bytes, *, content_type: str | None = None) -> None:
        data_path = self._resolve_storage_path(storage_key)
        data_path.parent.mkdir(parents=True, exist_ok=True)
        data_path.write_bytes(data)
        metadata_path = self._metadata_path(data_path)
        metadata_path.write_text(
            json.dumps({"content_type": content_type, "updated_at": datetime.now(UTC).isoformat()}),
            encoding="utf-8",
        )

    def assert_storage_available(self) -> None:
        probe_path = self._root / ".healthcheck"
        try:
            probe_path.write_text("ok", encoding="utf-8")
            probe_path.unlink(missing_ok=True)
        except Exception as exc:  # pragma: no cover - runtime/environment check
            raise ProviderRequestError(
                code="object_storage_unavailable",
                message=f"Local object storage directory is unavailable: {exc}",
                status_code=503,
                retryable=True,
                provider="local_storage",
            ) from exc

    def resolve_upload_token(self, token: str) -> LocalSignedStorageRequest:
        payload = self._resolve_token(token, expected_operation="upload")
        return LocalSignedStorageRequest(
            storage_key=str(payload["storage_key"]),
            mime_type=payload.get("mime_type"),
        )

    def resolve_download_token(self, token: str) -> LocalSignedStorageRequest:
        payload = self._resolve_token(token, expected_operation="download")
        return LocalSignedStorageRequest(
            storage_key=str(payload["storage_key"]),
            mime_type=payload.get("mime_type"),
        )

    def _issue_token(self, *, storage_key: str, operation: str, mime_type: str | None = None) -> str:
        expires_at = int(datetime.now(UTC).timestamp()) + settings.signed_url_ttl_seconds
        payload = {"storage_key": storage_key, "operation": operation, "expires_at": expires_at}
        if mime_type:
            payload["mime_type"] = mime_type
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload_encoded = _b64url_encode(payload_bytes)
        signature = hmac.new(self._secret, payload_encoded.encode("utf-8"), hashlib.sha256).digest()
        return f"{payload_encoded}.{_b64url_encode(signature)}"

    def _resolve_token(self, token: str, *, expected_operation: str) -> dict:
        try:
            payload_encoded, signature_encoded = token.split(".", maxsplit=1)
        except ValueError as exc:
            raise ProviderRequestError(
                code="invalid_storage_token",
                message="Malformed storage token",
                status_code=401,
                retryable=False,
                provider="local_storage",
            ) from exc
        expected_signature = hmac.new(self._secret, payload_encoded.encode("utf-8"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected_signature, _b64url_decode(signature_encoded)):
            raise ProviderRequestError(
                code="invalid_storage_token",
                message="Storage token signature is invalid",
                status_code=401,
                retryable=False,
                provider="local_storage",
            )
        try:
            payload = json.loads(_b64url_decode(payload_encoded).decode("utf-8"))
        except Exception as exc:
            raise ProviderRequestError(
                code="invalid_storage_token",
                message="Storage token payload is invalid",
                status_code=401,
                retryable=False,
                provider="local_storage",
            ) from exc
        expires_at = int(payload.get("expires_at", 0))
        if expires_at < int(datetime.now(UTC).timestamp()):
            raise ProviderRequestError(
                code="expired_storage_token",
                message="Storage token has expired",
                status_code=401,
                retryable=False,
                provider="local_storage",
            )
        if payload.get("operation") != expected_operation:
            raise ProviderRequestError(
                code="invalid_storage_token",
                message="Storage token operation is invalid",
                status_code=401,
                retryable=False,
                provider="local_storage",
            )
        storage_key = payload.get("storage_key")
        if not isinstance(storage_key, str) or not storage_key.strip():
            raise ProviderRequestError(
                code="invalid_storage_token",
                message="Storage token does not contain a valid storage key",
                status_code=401,
                retryable=False,
                provider="local_storage",
            )
        return payload

    def _resolve_storage_path(self, storage_key: str) -> Path:
        normalized = storage_key.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part and part != "."]
        if any(part == ".." for part in parts):
            raise ProviderRequestError(
                code="invalid_storage_key",
                message="Storage key cannot include parent-directory traversal",
                status_code=400,
                retryable=False,
                provider="local_storage",
            )
        relative_path = Path(*parts)
        full_path = (self._root / relative_path).resolve()
        if self._root not in full_path.parents and full_path != self._root:
            raise ProviderRequestError(
                code="invalid_storage_key",
                message="Storage key is outside of the configured local storage root",
                status_code=400,
                retryable=False,
                provider="local_storage",
            )
        return full_path

    def _metadata_path(self, data_path: Path) -> Path:
        return data_path.with_suffix(f"{data_path.suffix}.meta.json")

    def _read_content_type(self, data_path: Path) -> str | None:
        metadata_path = self._metadata_path(data_path)
        if not metadata_path.exists():
            return None
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        content_type = payload.get("content_type")
        return content_type if isinstance(content_type, str) and content_type.strip() else None
