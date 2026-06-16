from __future__ import annotations

import json
from urllib import error, request

from app.settings import settings


class EmbeddingProviderError(RuntimeError):
    pass


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    try:
        return _embed_with_openrouter(texts)
    except EmbeddingProviderError:
        if not settings.gemini_fallback_enabled:
            raise
        return _embed_with_gemini(texts)


def _embed_with_openrouter(texts: list[str]) -> list[list[float]]:
    if not settings.openrouter_api_key:
        raise EmbeddingProviderError("CURRICULUM_TUTOR_OPENROUTER_API_KEY is required for OpenRouter embeddings")
    payload = {
        "model": settings.openrouter_embedding_model,
        "input": texts,
        "encoding_format": "float",
    }
    req = request.Request(
        f"{settings.openrouter_base_url.rstrip('/')}/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=_headers(),
    )
    try:
        with request.urlopen(req, timeout=60.0) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:  # pragma: no cover - provider failure
        detail = exc.read().decode("utf-8", errors="replace")
        raise EmbeddingProviderError(f"OpenRouter embeddings request failed with status {exc.code}: {detail}") from exc
    except error.URLError as exc:  # pragma: no cover - provider failure
        raise EmbeddingProviderError(f"OpenRouter embeddings request failed: {exc.reason}") from exc
    parsed = json.loads(raw) if raw else {}
    data = parsed.get("data")
    if not isinstance(data, list):
        raise EmbeddingProviderError("OpenRouter embeddings response was malformed")
    vectors: list[list[float]] = []
    for item in data:
        embedding = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(embedding, list):
            raise EmbeddingProviderError("OpenRouter embeddings response missing embedding vector")
        vectors.append([float(value) for value in embedding])
    return vectors


def _embed_with_gemini(texts: list[str]) -> list[list[float]]:
    if not settings.gemini_api_key:
        raise EmbeddingProviderError("OpenRouter embeddings failed and CURRICULUM_TUTOR_GEMINI_API_KEY is not configured")
    model = _gemini_model_resource(settings.gemini_embedding_model)
    payload = {
        "requests": [
            {
                "model": model,
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": settings.gemini_embedding_output_dimensionality,
            }
            for text in texts
        ]
    }
    req = request.Request(
        f"{settings.gemini_base_url.rstrip('/')}/{model}:batchEmbedContents",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "x-goog-api-key": settings.gemini_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=60.0) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:  # pragma: no cover - provider failure
        detail = exc.read().decode("utf-8", errors="replace")
        raise EmbeddingProviderError(f"Gemini embeddings request failed with status {exc.code}: {detail}") from exc
    except error.URLError as exc:  # pragma: no cover - provider failure
        raise EmbeddingProviderError(f"Gemini embeddings request failed: {exc.reason}") from exc
    parsed = json.loads(raw) if raw else {}
    embeddings = parsed.get("embeddings")
    if not isinstance(embeddings, list):
        raise EmbeddingProviderError("Gemini embeddings response was malformed")
    vectors: list[list[float]] = []
    for item in embeddings:
        values = item.get("values") if isinstance(item, dict) else None
        if not isinstance(values, list):
            raise EmbeddingProviderError("Gemini embeddings response missing embedding values")
        vectors.append([float(value) for value in values])
    if len(vectors) != len(texts):
        raise EmbeddingProviderError("Gemini embeddings response missing one or more vectors")
    return vectors


def _gemini_model_resource(model: str) -> str:
    return model if model.startswith("models/") else f"models/{model}"


def _headers() -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if settings.openrouter_http_referer:
        headers["HTTP-Referer"] = settings.openrouter_http_referer
    if settings.openrouter_title:
        headers["X-Title"] = settings.openrouter_title
    return headers
