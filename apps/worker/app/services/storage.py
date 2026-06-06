from __future__ import annotations

from pathlib import Path

from app.settings import settings


class StorageError(RuntimeError):
    pass


PRIMARY_STORAGE_BACKENDS = {"s3", "r2"}
SUPPORTED_STORAGE_BACKENDS = {"auto", "local", *PRIMARY_STORAGE_BACKENDS}


def get_object_bytes(storage_key: str) -> bytes:
    backend = settings.object_storage_backend.strip().lower()
    if backend == "local":
        return _read_local(storage_key)

    if backend not in SUPPORTED_STORAGE_BACKENDS:
        raise StorageError(f"Unsupported object storage backend '{settings.object_storage_backend}'")

    try:
        return _read_s3(storage_key)
    except Exception:
        if settings.object_storage_local_fallback_enabled:
            return _read_local(storage_key)
        raise


def _read_s3(storage_key: str) -> bytes:
    if not settings.s3_endpoint or not settings.s3_bucket:
        raise StorageError("Primary object storage is not configured")
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise StorageError("boto3 is required for worker object storage access") from exc

    session = boto3.session.Session()
    client = session.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        use_ssl=settings.s3_use_ssl,
        region_name=settings.s3_region,
        config=BotoConfig(
            connect_timeout=settings.s3_connect_timeout_seconds,
            read_timeout=settings.s3_read_timeout_seconds,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    )
    try:
        response = client.get_object(Bucket=settings.s3_bucket, Key=storage_key)
    except Exception as exc:  # pragma: no cover - runtime/provider failure
        raise StorageError(f"Primary object storage read failed: {exc}") from exc
    return response["Body"].read()


def _read_local(storage_key: str) -> bytes:
    root = Path(settings.object_storage_local_dir).resolve()
    normalized = storage_key.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise StorageError("Storage key contains parent-directory traversal")
    file_path = (root / Path(*parts)).resolve()
    if root not in file_path.parents and file_path != root:
        raise StorageError("Storage key resolves outside the configured local storage root")
    if not file_path.exists() or not file_path.is_file():
        raise StorageError(f"Local storage object '{storage_key}' was not found")
    return file_path.read_bytes()
