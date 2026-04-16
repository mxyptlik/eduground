from __future__ import annotations

from app.core.config import settings


class RuntimeConfigurationError(RuntimeError):
    pass


def _is_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip().lower()
    if not normalized:
        return True
    placeholder_markers = ("replace_me", "placeholder", "dummy", "your-", "set-me")
    return any(marker in normalized for marker in placeholder_markers)


def _require(value: str | None, env_name: str) -> None:
    if _is_placeholder(value):
        raise RuntimeConfigurationError(f"{env_name} must be configured with a real value")


def _require_present(value: str | None, env_name: str) -> None:
    if value is None or not value.strip():
        raise RuntimeConfigurationError(f"{env_name} must be configured")


def validate_runtime_configuration() -> None:
    _require(settings.secret_key, "CURRICULUM_TUTOR_SECRET_KEY")
    _require_present(settings.database_url, "CURRICULUM_TUTOR_DATABASE_URL")

    if settings.clerk_enabled:
        _require(settings.clerk_secret_key, "CURRICULUM_TUTOR_CLERK_SECRET_KEY")
        _require(settings.clerk_publishable_key, "CURRICULUM_TUTOR_CLERK_PUBLISHABLE_KEY")
        _require(settings.clerk_jwks_url, "CURRICULUM_TUTOR_CLERK_JWKS_URL")
        _require(settings.clerk_jwt_issuer, "CURRICULUM_TUTOR_CLERK_JWT_ISSUER")

    if settings.otel_enabled:
        _require_present(settings.otel_exporter_otlp_endpoint, "CURRICULUM_TUTOR_OTEL_EXPORTER_OTLP_ENDPOINT")

    _require(settings.openrouter_api_key, "CURRICULUM_TUTOR_OPENROUTER_API_KEY")
    _require(settings.openrouter_chat_model, "CURRICULUM_TUTOR_OPENROUTER_CHAT_MODEL")
    _require(settings.openrouter_embedding_model, "CURRICULUM_TUTOR_OPENROUTER_EMBEDDING_MODEL")

    _require(settings.qdrant_url, "CURRICULUM_TUTOR_QDRANT_URL")
    _require(settings.qdrant_collection_name, "CURRICULUM_TUTOR_QDRANT_COLLECTION_NAME")

    storage_backend = settings.object_storage_backend.strip().lower()
    if storage_backend not in {"auto", "r2", "local"}:
        raise RuntimeConfigurationError("CURRICULUM_TUTOR_OBJECT_STORAGE_BACKEND must be one of: auto, r2, local")

    s3_values = [settings.s3_endpoint, settings.s3_bucket, settings.s3_access_key, settings.s3_secret_key]
    s3_all_present = all(value is not None and str(value).strip() for value in s3_values)

    if storage_backend == "r2":
        _require_present(settings.s3_endpoint, "CURRICULUM_TUTOR_S3_ENDPOINT")
        _require_present(settings.s3_bucket, "CURRICULUM_TUTOR_S3_BUCKET")
        _require_present(settings.s3_access_key, "CURRICULUM_TUTOR_S3_ACCESS_KEY")
        _require_present(settings.s3_secret_key, "CURRICULUM_TUTOR_S3_SECRET_KEY")

    if storage_backend == "auto":
        if s3_all_present:
            _require_present(settings.s3_endpoint, "CURRICULUM_TUTOR_S3_ENDPOINT")
            _require_present(settings.s3_bucket, "CURRICULUM_TUTOR_S3_BUCKET")
            _require_present(settings.s3_access_key, "CURRICULUM_TUTOR_S3_ACCESS_KEY")
            _require_present(settings.s3_secret_key, "CURRICULUM_TUTOR_S3_SECRET_KEY")
        elif not settings.object_storage_local_fallback_enabled:
            raise RuntimeConfigurationError(
                "Auto object storage mode requires either full CURRICULUM_TUTOR_S3_* configuration or local fallback enabled"
            )

    if storage_backend == "local" or (storage_backend == "auto" and settings.object_storage_local_fallback_enabled):
        _require_present(settings.object_storage_local_dir, "CURRICULUM_TUTOR_OBJECT_STORAGE_LOCAL_DIR")
        _require_present(settings.api_public_base_url, "CURRICULUM_TUTOR_API_PUBLIC_BASE_URL")

    if settings.reranker_enabled:
        _require(settings.openrouter_api_key, "CURRICULUM_TUTOR_OPENROUTER_API_KEY")
        _require(settings.openrouter_rerank_model, "CURRICULUM_TUTOR_OPENROUTER_RERANK_MODEL")

    _require(settings.unstructured_api_key, "CURRICULUM_TUTOR_UNSTRUCTURED_API_KEY")
