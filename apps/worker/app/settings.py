from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CURRICULUM_TUTOR_", extra="ignore")

    database_url: str = "postgresql://localhost/eduground"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection_name: str = "eduground_chunks"
    qdrant_timeout_seconds: float = 60.0
    object_storage_backend: str = "auto"
    object_storage_local_fallback_enabled: bool = True
    object_storage_local_dir: str = ".eduground_local_storage"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "curriculum-tutor"
    s3_use_ssl: bool = False
    s3_region: str = "auto"
    s3_connect_timeout_seconds: float = 2.0
    s3_read_timeout_seconds: float = 2.0
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_embedding_model: str = "openai/text-embedding-3-large"
    openrouter_http_referer: str | None = None
    openrouter_title: str | None = None
    gemini_fallback_enabled: bool = True
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_output_dimensionality: int = 3072
    unstructured_api_key: str | None = None
    unstructured_api_url: str = "https://api.unstructuredapp.io/general/v0/general"
    unstructured_ocr_strategy: str = "hi_res"
    ocr_vision_enabled: bool = False
    ocr_vision_model: str = "deepseek/deepseek-v4.1-flash"
    ocr_vision_max_pages: int = 100
    ocr_vision_max_output_tokens: int = 4096
    ocr_density_threshold: float = 0.15
    chunk_min_tokens: int = 300
    chunk_max_tokens: int = 500
    chunk_overlap_tokens: int = 60
    ingestion_batch_enabled: bool = False
    ingestion_batch_window_seconds: int = 20
    ingestion_batch_max_files: int = 32
    ingestion_batch_max_embed_batch: int = 64
    ingestion_batch_workers: int = 8
    embedding_max_inputs: int = 64
    embedding_max_input_bytes: int = 65536
    vector_upsert_max_points: int = 128
    parsing_timeout_seconds: float = 300.0
    parsing_isolation_enabled: bool = False
    visual_enabled: bool = False
    visual_provider: str | None = None
    visual_model: str | None = None
    visual_fallback_provider: str | None = None
    visual_fallback_model: str | None = None
    visual_max_figures_per_document: int = 40
    visual_max_image_bytes: int = 8_388_608
    visual_min_area_fraction: float = 0.02
    visual_max_output_tokens: int = 1024
    visual_batch_size: int = 1
    visual_cache_dir: str = ".eduground_visual_cache"
    log_level: str = "INFO"
    otel_enabled: bool = False
    otel_service_name: str = "curriculum-tutor-worker"
    otel_service_namespace: str = "eduground"


@lru_cache(maxsize=1)
def get_settings() -> WorkerSettings:
    return WorkerSettings()


settings = get_settings()
