from __future__ import annotations

from app.core.config import settings
from app.core.errors import DependencyUnavailableError, ProviderRequestError
from app.core.observability import traced_operation
from app.integrations.storage.base import ObjectStorage, StoredObjectMetadata


class S3ObjectStorage(ObjectStorage):
    def __init__(self) -> None:
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError("boto3 is required for the real S3/R2 object storage adapter") from exc
        if not settings.s3_endpoint or not settings.s3_bucket:
            raise DependencyUnavailableError(
                code="object_storage_not_configured",
                message="Object storage is not fully configured",
                provider="r2",
            )

        session = boto3.session.Session()
        endpoint_url = settings.s3_endpoint
        self._client = session.client(
            "s3",
            endpoint_url=endpoint_url,
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
        self._bucket = settings.s3_bucket

    def create_signed_upload_url(self, storage_key: str, mime_type: str) -> str:
        with traced_operation(
            "provider.r2.create_signed_upload_url",
            metric_name="eduground_provider_call",
            metric_labels={"provider": "r2", "operation": "create_signed_upload_url"},
        ):
            return self._client.generate_presigned_url(
                ClientMethod="put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": storage_key,
                    "ContentType": mime_type,
                },
                ExpiresIn=settings.signed_url_ttl_seconds,
            )

    def create_signed_download_url(self, storage_key: str) -> str:
        with traced_operation(
            "provider.r2.create_signed_download_url",
            metric_name="eduground_provider_call",
            metric_labels={"provider": "r2", "operation": "create_signed_download_url"},
        ):
            return self._client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self._bucket, "Key": storage_key},
                ExpiresIn=settings.signed_url_ttl_seconds,
            )

    def head_object(self, storage_key: str) -> StoredObjectMetadata | None:
        try:
            with traced_operation(
                "provider.r2.head_object",
                metric_name="eduground_provider_call",
                metric_labels={"provider": "r2", "operation": "head_object"},
            ):
                response = self._client.head_object(Bucket=self._bucket, Key=storage_key)
        except self._client.exceptions.NoSuchKey:
            return None
        except Exception as exc:
            error_code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", "")).strip()
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise ProviderRequestError(
                code="object_storage_head_failed",
                message=f"Object storage HEAD failed: {exc}",
                status_code=502,
                retryable=True,
                provider="r2",
            ) from exc
        return StoredObjectMetadata(
            storage_key=storage_key,
            byte_size=int(response["ContentLength"]),
            etag=str(response.get("ETag", "")).strip('"') or None,
            content_type=response.get("ContentType"),
        )

    def get_object_bytes(self, storage_key: str) -> bytes:
        try:
            with traced_operation(
                "provider.r2.get_object",
                metric_name="eduground_provider_call",
                metric_labels={"provider": "r2", "operation": "get_object"},
            ):
                response = self._client.get_object(Bucket=self._bucket, Key=storage_key)
            return response["Body"].read()
        except Exception as exc:  # pragma: no cover - exercised in integration/runtime
            raise ProviderRequestError(
                code="object_storage_read_failed",
                message=f"Object storage read failed: {exc}",
                status_code=502,
                retryable=True,
                provider="r2",
            ) from exc

    def assert_bucket_available(self) -> None:
        try:
            with traced_operation(
                "provider.r2.head_bucket",
                metric_name="eduground_provider_call",
                metric_labels={"provider": "r2", "operation": "head_bucket"},
            ):
                self._client.head_bucket(Bucket=self._bucket)
        except Exception as exc:  # pragma: no cover - exercised through readiness
            raise ProviderRequestError(
                code="object_storage_bucket_unavailable",
                message=f"Object storage bucket check failed: {exc}",
                status_code=503,
                retryable=True,
                provider="r2",
            ) from exc
