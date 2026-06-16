from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    api_prefix: str = Field(default="/api", validation_alias=AliasChoices("CURRICULUM_TUTOR_API_PREFIX", "API_PREFIX"))
    app_env: str = Field(default="development", validation_alias=AliasChoices("CURRICULUM_TUTOR_APP_ENV", "APP_ENV"))
    app_name: str = Field(default="Eduground API", validation_alias=AliasChoices("CURRICULUM_TUTOR_APP_NAME", "APP_NAME"))
    secret_key: str = Field(default="change-me", validation_alias=AliasChoices("CURRICULUM_TUTOR_SECRET_KEY", "SECRET_KEY"))
    database_url: str = Field(validation_alias=AliasChoices("CURRICULUM_TUTOR_DATABASE_URL", "DATABASE_URL"))
    database_pool_size: int = Field(
        default=20,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_DATABASE_POOL_SIZE", "DATABASE_POOL_SIZE"),
    )
    database_max_overflow: int = Field(
        default=40,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_DATABASE_MAX_OVERFLOW", "DATABASE_MAX_OVERFLOW"),
    )
    database_pool_timeout_seconds: int = Field(
        default=30,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_DATABASE_POOL_TIMEOUT_SECONDS", "DATABASE_POOL_TIMEOUT_SECONDS"),
    )
    database_pool_recycle_seconds: int = Field(
        default=1800,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_DATABASE_POOL_RECYCLE_SECONDS", "DATABASE_POOL_RECYCLE_SECONDS"),
    )
    access_token_ttl_minutes: int = 60
    session_cookie_name: str = Field(
        default="eduground_session",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_SESSION_COOKIE_NAME", "SESSION_COOKIE_NAME"),
    )
    session_cookie_secure: bool = Field(
        default=False,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_SESSION_COOKIE_SECURE", "SESSION_COOKIE_SECURE"),
    )
    session_cookie_domain: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_SESSION_COOKIE_DOMAIN", "SESSION_COOKIE_DOMAIN"),
    )
    web_base_url: str = Field(
        default="http://localhost:5173",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_WEB_BASE_URL", "APP_BASE_URL", "WEB_BASE_URL"),
    )
    api_public_base_url: str = Field(
        default="http://localhost:8000",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_API_PUBLIC_BASE_URL", "API_PUBLIC_BASE_URL"),
    )
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:3000",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CORS_ORIGINS", "CORS_ORIGINS"),
    )
    trusted_hosts: str = Field(
        default="localhost,127.0.0.1,testserver,host.docker.internal",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_TRUSTED_HOSTS", "TRUSTED_HOSTS"),
    )
    auto_create_schema: bool = Field(
        default=True,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_AUTO_CREATE_SCHEMA", "AUTO_CREATE_SCHEMA"),
    )
    s3_bucket: str = Field(
        default="curriculum-tutor",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_S3_BUCKET", "MINIO_BUCKET", "S3_BUCKET"),
    )
    s3_endpoint: str = Field(
        default="http://localhost:9000",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_S3_ENDPOINT", "S3_ENDPOINT", "MINIO_ENDPOINT"),
    )
    s3_access_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_S3_ACCESS_KEY", "MINIO_ACCESS_KEY", "AWS_ACCESS_KEY_ID"),
    )
    s3_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_S3_SECRET_KEY", "MINIO_SECRET_KEY", "AWS_SECRET_ACCESS_KEY"),
    )
    s3_use_ssl: bool = Field(
        default=False,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_S3_USE_SSL", "MINIO_USE_SSL"),
    )
    s3_region: str = Field(
        default="auto",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_S3_REGION", "AWS_REGION", "S3_REGION"),
    )
    s3_connect_timeout_seconds: float = Field(
        default=2.0,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_S3_CONNECT_TIMEOUT_SECONDS", "S3_CONNECT_TIMEOUT_SECONDS"),
    )
    s3_read_timeout_seconds: float = Field(
        default=2.0,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_S3_READ_TIMEOUT_SECONDS", "S3_READ_TIMEOUT_SECONDS"),
    )
    object_storage_backend: str = Field(
        default="auto",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OBJECT_STORAGE_BACKEND", "OBJECT_STORAGE_BACKEND"),
    )
    object_storage_local_fallback_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OBJECT_STORAGE_LOCAL_FALLBACK_ENABLED", "OBJECT_STORAGE_LOCAL_FALLBACK_ENABLED"),
    )
    object_storage_local_dir: str = Field(
        default=".eduground_local_storage",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OBJECT_STORAGE_LOCAL_DIR", "OBJECT_STORAGE_LOCAL_DIR"),
    )
    object_storage_primary_health_ttl_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "CURRICULUM_TUTOR_OBJECT_STORAGE_PRIMARY_HEALTH_TTL_SECONDS",
            "OBJECT_STORAGE_PRIMARY_HEALTH_TTL_SECONDS",
        ),
    )
    object_storage_primary_failure_backoff_seconds: float = Field(
        default=300.0,
        validation_alias=AliasChoices(
            "CURRICULUM_TUTOR_OBJECT_STORAGE_PRIMARY_FAILURE_BACKOFF_SECONDS",
            "OBJECT_STORAGE_PRIMARY_FAILURE_BACKOFF_SECONDS",
        ),
    )
    qdrant_url: str = Field(
        default="http://localhost:6333",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_QDRANT_URL", "QDRANT_URL"),
    )
    qdrant_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_QDRANT_API_KEY", "QDRANT_API_KEY"),
    )
    qdrant_collection_name: str = Field(
        default="eduground_chunks",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_QDRANT_COLLECTION_NAME", "QDRANT_COLLECTION_NAME"),
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_REDIS_URL", "REDIS_URL"),
    )
    chat_memory_ttl_seconds: int = Field(
        default=86_400,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CHAT_MEMORY_TTL_SECONDS", "CHAT_MEMORY_TTL_SECONDS"),
    )
    max_upload_bytes: int = Field(
        default=50_000_000,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_MAX_UPLOAD_BYTES", "MAX_UPLOAD_BYTES"),
    )
    max_api_request_bytes: int = Field(
        default=1_000_000,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_MAX_API_REQUEST_BYTES", "MAX_API_REQUEST_BYTES"),
    )
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_LOG_LEVEL", "LOG_LEVEL"),
    )
    log_json: bool = Field(
        default=True,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_LOG_JSON", "LOG_JSON"),
    )
    otel_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OTEL_ENABLED", "OTEL_ENABLED"),
    )
    otel_exporter_otlp_endpoint: str = Field(
        default="http://localhost:4318/v1/traces",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"),
    )
    otel_exporter_otlp_headers: str = Field(
        default="",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OTEL_EXPORTER_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_HEADERS"),
    )
    otel_exporter_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OTEL_EXPORTER_TIMEOUT_SECONDS", "OTEL_EXPORTER_TIMEOUT_SECONDS"),
    )
    otel_trace_sample_ratio: float = Field(
        default=1.0,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OTEL_TRACE_SAMPLE_RATIO", "OTEL_TRACE_SAMPLE_RATIO"),
    )
    signed_url_ttl_seconds: int = Field(
        default=900,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_SIGNED_URL_TTL_SECONDS", "SIGNED_URL_TTL_SECONDS"),
    )
    auth_rate_limit_requests: int = Field(
        default=10,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_AUTH_RATE_LIMIT_REQUESTS", "AUTH_RATE_LIMIT_REQUESTS"),
    )
    auth_rate_limit_window_seconds: int = Field(
        default=60,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_AUTH_RATE_LIMIT_WINDOW_SECONDS", "AUTH_RATE_LIMIT_WINDOW_SECONDS"),
    )
    clerk_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_ENABLED", "CLERK_ENABLED"),
    )
    clerk_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_SECRET_KEY", "CLERK_SECRET_KEY"),
    )
    clerk_api_url: str = Field(
        default="https://api.clerk.com/v1",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_API_URL", "CLERK_API_URL"),
    )
    clerk_http_timeout_seconds: float = Field(
        default=8.0,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_HTTP_TIMEOUT_SECONDS", "CLERK_HTTP_TIMEOUT_SECONDS"),
    )
    clerk_publishable_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_PUBLISHABLE_KEY", "CLERK_PUBLISHABLE_KEY"),
    )
    clerk_jwks_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_JWKS_URL", "CLERK_JWKS_URL"),
    )
    clerk_jwt_issuer: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_JWT_ISSUER", "CLERK_JWT_ISSUER"),
    )
    clerk_jwt_leeway_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_JWT_LEEWAY_SECONDS", "CLERK_JWT_LEEWAY_SECONDS"),
    )
    clerk_authorized_parties: str = Field(
        default="",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_AUTHORIZED_PARTIES", "CLERK_AUTHORIZED_PARTIES"),
    )
    clerk_webhook_signing_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_WEBHOOK_SIGNING_SECRET", "CLERK_WEBHOOK_SIGNING_SECRET"),
    )
    clerk_active_organization_header: str = Field(
        default="X-Active-Organization-Id",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_ACTIVE_ORGANIZATION_HEADER", "CLERK_ACTIVE_ORGANIZATION_HEADER"),
    )
    clerk_session_cookie_name: str = Field(
        default="__session",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_SESSION_COOKIE_NAME", "CLERK_SESSION_COOKIE_NAME"),
    )
    clerk_sync_read_repair: bool = Field(
        default=True,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_CLERK_SYNC_READ_REPAIR", "CLERK_SYNC_READ_REPAIR"),
    )
    openrouter_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OPENROUTER_BASE_URL", "OPENROUTER_BASE_URL"),
    )
    openrouter_chat_model: str = Field(
        default="openai/gpt-4.1-mini",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OPENROUTER_CHAT_MODEL", "OPENROUTER_CHAT_MODEL"),
    )
    openrouter_rerank_model: str = Field(
        default="openai/gpt-4.1-mini",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OPENROUTER_RERANK_MODEL", "OPENROUTER_RERANK_MODEL"),
    )
    openrouter_embedding_model: str = Field(
        default="openai/text-embedding-3-large",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OPENROUTER_EMBEDDING_MODEL", "OPENROUTER_EMBEDDING_MODEL"),
    )
    openrouter_http_referer: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OPENROUTER_HTTP_REFERER", "OPENROUTER_HTTP_REFERER"),
    )
    openrouter_title: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_OPENROUTER_TITLE", "OPENROUTER_TITLE"),
    )
    gemini_fallback_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_GEMINI_FALLBACK_ENABLED", "GEMINI_FALLBACK_ENABLED"),
    )
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_GEMINI_API_KEY", "GEMINI_API_KEY"),
    )
    gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_GEMINI_BASE_URL", "GEMINI_BASE_URL"),
    )
    gemini_chat_model: str = Field(
        default="gemini-2.0-flash",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_GEMINI_CHAT_MODEL", "GEMINI_CHAT_MODEL"),
    )
    gemini_embedding_model: str = Field(
        default="gemini-embedding-001",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_GEMINI_EMBEDDING_MODEL", "GEMINI_EMBEDDING_MODEL"),
    )
    gemini_embedding_output_dimensionality: int = Field(
        default=3072,
        validation_alias=AliasChoices(
            "CURRICULUM_TUTOR_GEMINI_EMBEDDING_OUTPUT_DIMENSIONALITY",
            "GEMINI_EMBEDDING_OUTPUT_DIMENSIONALITY",
        ),
    )
    reranker_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_RERANKER_ENABLED", "RERANKER_ENABLED"),
    )
    reranker_top_n: int = Field(
        default=12,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_RERANKER_TOP_N", "RERANKER_TOP_N"),
    )
    unstructured_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CURRICULUM_TUTOR_UNSTRUCTURED_API_KEY", "UNSTRUCTURED_API_KEY"),
    )
    unstructured_api_url: str = Field(
        default="https://api.unstructured.io/general/v0/general",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_UNSTRUCTURED_API_URL", "UNSTRUCTURED_API_URL"),
    )
    unstructured_ocr_strategy: str = Field(
        default="ocr_only",
        validation_alias=AliasChoices("CURRICULUM_TUTOR_UNSTRUCTURED_OCR_STRATEGY", "UNSTRUCTURED_OCR_STRATEGY"),
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        hosts = [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]
        for required_host in ("localhost", "127.0.0.1", "testserver", "host.docker.internal"):
            if required_host not in hosts:
                hosts.append(required_host)
        return hosts

    @property
    def clerk_authorized_party_list(self) -> list[str]:
        return [party.strip() for party in self.clerk_authorized_parties.split(",") if party.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
