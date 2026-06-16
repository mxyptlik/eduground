from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import json
from urllib import error, request

from app.core.config import settings
from app.core.errors import DependencyUnavailableError, ProviderRequestError
from app.core.logging import get_logger
from app.core.observability import traced_operation

logger = get_logger("app.embeddings")


class EmbeddingProviderError(ProviderRequestError):
    def __init__(self, message: str, *, status_code: int = 502, retryable: bool = True, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="openrouter_embedding_error",
            message=message,
            status_code=status_code,
            retryable=retryable,
            provider="openrouter",
            details=details or {},
        )


class GeminiEmbeddingProviderError(ProviderRequestError):
    def __init__(self, message: str, *, status_code: int = 502, retryable: bool = True, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="gemini_embedding_error",
            message=message,
            status_code=status_code,
            retryable=retryable,
            provider="gemini",
            details=details or {},
        )


@dataclass(slots=True)
class OpenRouterEmbeddingConfig:
    api_key: str | None = None
    model: str = "openai/text-embedding-3-large"
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 60.0
    site_url: str | None = None
    app_name: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "OpenRouterEmbeddingConfig":
        defaults = cls()
        return cls(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_embedding_model or defaults.model,
            base_url=settings.openrouter_base_url or defaults.base_url,
            timeout_seconds=defaults.timeout_seconds,
            site_url=settings.openrouter_http_referer or settings.web_base_url,
            app_name=settings.openrouter_title or settings.app_name,
        )


@dataclass(slots=True)
class GeminiEmbeddingConfig:
    api_key: str | None = None
    model: str = "gemini-embedding-001"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: float = 60.0
    output_dimensionality: int = 3072

    @classmethod
    def from_env(cls) -> "GeminiEmbeddingConfig":
        defaults = cls()
        return cls(
            api_key=settings.gemini_api_key,
            model=settings.gemini_embedding_model or defaults.model,
            base_url=settings.gemini_base_url or defaults.base_url,
            timeout_seconds=defaults.timeout_seconds,
            output_dimensionality=settings.gemini_embedding_output_dimensionality or defaults.output_dimensionality,
        )


def _request_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float,
    *,
    provider: str = "openrouter",
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with traced_operation(
            f"provider.{provider}.embeddings.request",
            metric_name="eduground_provider_call",
            metric_labels={"provider": provider, "operation": "embeddings.embed"},
        ):
            with request.urlopen(req, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
    except error.HTTPError as exc:  # pragma: no cover - exercised through integration tests
        detail = exc.read().decode("utf-8", errors="replace")
        error_type = GeminiEmbeddingProviderError if provider == "gemini" else EmbeddingProviderError
        provider_name = "Gemini" if provider == "gemini" else "OpenRouter"
        raise error_type(
            f"{provider_name} embeddings request failed with status {exc.code}: {detail}",
            status_code=502 if exc.code >= 500 else 424,
            retryable=exc.code >= 500 or exc.code == 429,
            details={"upstream_status_code": exc.code},
        ) from exc
    except error.URLError as exc:  # pragma: no cover - exercised through integration tests
        if provider == "gemini":
            raise GeminiEmbeddingProviderError(f"Gemini embeddings request failed: {exc.reason}") from exc
        raise EmbeddingProviderError(f"OpenRouter embeddings request failed: {exc.reason}") from exc
    return json.loads(raw) if raw else {}


def _headers_from_config(config: OpenRouterEmbeddingConfig) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config.api_key}" if config.api_key else "",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if config.site_url:
        headers["HTTP-Referer"] = config.site_url
    if config.app_name:
        headers["X-Title"] = config.app_name
    headers.update(config.extra_headers)
    return {key: value for key, value in headers.items() if value}


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class OpenRouterEmbeddingProvider(EmbeddingProvider):
    def __init__(self, config: OpenRouterEmbeddingConfig | None = None, fallback: EmbeddingProvider | None = None) -> None:
        self.config = config or OpenRouterEmbeddingConfig.from_env()
        self.fallback = fallback or (GeminiEmbeddingProvider() if settings.gemini_fallback_enabled else None)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            if not self.config.api_key:
                raise DependencyUnavailableError(
                    code="openrouter_api_key_missing",
                    message="OpenRouter API key is not configured",
                    provider="openrouter",
                )
            response = _request_json(
                f"{self.config.base_url.rstrip('/')}/embeddings",
                {"model": self.config.model, "input": texts},
                _headers_from_config(self.config),
                self.config.timeout_seconds,
            )
            return _extract_openrouter_embeddings(response, len(texts))
        except (DependencyUnavailableError, EmbeddingProviderError) as exc:
            if self.fallback is None:
                raise
            try:
                logger.warning(
                    "OpenRouter embeddings failed; falling back to Gemini",
                    extra={"extra_json": {"error": str(exc), "fallback_provider": "gemini"}},
                )
                return self.fallback.embed(texts)
            except DependencyUnavailableError:
                raise exc


def _extract_openrouter_embeddings(response: dict[str, Any], expected_count: int) -> list[list[float]]:
    data = response.get("data")
    if not isinstance(data, list):
        raise EmbeddingProviderError("OpenRouter embeddings response did not include a data array")
    vectors_by_index: dict[int, list[float]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        embedding = item.get("embedding")
        if isinstance(index, int) and isinstance(embedding, list):
            vectors_by_index[index] = [float(value) for value in embedding]
    if len(vectors_by_index) != expected_count:
        raise EmbeddingProviderError("OpenRouter embeddings response did not include an embedding for every input")
    return [vectors_by_index[index] for index in range(expected_count)]


def _gemini_model_resource(model: str) -> str:
    return model if model.startswith("models/") else f"models/{model}"


class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, config: GeminiEmbeddingConfig | None = None) -> None:
        self.config = config or GeminiEmbeddingConfig.from_env()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.config.api_key:
            raise DependencyUnavailableError(
                code="gemini_api_key_missing",
                message="Gemini API key is not configured",
                provider="gemini",
            )
        model = _gemini_model_resource(self.config.model)
        payload = {
            "requests": [
                {
                    "model": model,
                    "content": {"parts": [{"text": text}]},
                    "outputDimensionality": self.config.output_dimensionality,
                }
                for text in texts
            ]
        }
        response = _request_json(
            f"{self.config.base_url.rstrip('/')}/{model}:batchEmbedContents",
            payload,
            {
                "x-goog-api-key": self.config.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            self.config.timeout_seconds,
            provider="gemini",
        )
        embeddings = response.get("embeddings")
        if not isinstance(embeddings, list):
            raise GeminiEmbeddingProviderError("Gemini embeddings response did not include an embeddings array")
        vectors: list[list[float]] = []
        for item in embeddings:
            values = item.get("values") if isinstance(item, dict) else None
            if not isinstance(values, list):
                raise GeminiEmbeddingProviderError("Gemini embeddings response missing embedding values")
            vectors.append([float(value) for value in values])
        if len(vectors) != len(texts):
            raise GeminiEmbeddingProviderError("Gemini embeddings response did not include an embedding for every input")
        return vectors
