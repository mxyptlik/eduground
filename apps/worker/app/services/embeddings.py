from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from hashlib import sha256
from typing import Iterator
from urllib import error, request

from app.ledger import INGESTION_EMBEDDING_OPERATION, LedgerGuard
from app.settings import settings


class EmbeddingProviderError(RuntimeError):
    pass


def embed_texts(texts: list[str], *, guard: LedgerGuard | None = None) -> list[list[float]]:
    if not texts:
        return []
    session = _BATCH_SESSION.get()
    if session is not None:
        cached = session.lookup(texts)
        if cached is not None and len(cached) == len(texts):
            return cached
    groups = list(_embedding_groups(texts))
    if guard is not None:
        # Controlled runs pin one embedding model for the frozen corpus; a
        # fallback would silently change embedding identity mid-run.
        return [vector for group in groups for vector in _embed_with_openrouter(group, guard=guard)]
    try:
        return [vector for group in groups for vector in _embed_with_openrouter(group)]
    except EmbeddingProviderError:
        if not settings.gemini_fallback_enabled:
            raise
        # Re-embed all groups on fallback: never mix model vector spaces.
        return [vector for group in groups for vector in _embed_with_gemini(group)]


def _embedding_groups(texts: list[str]):
    batch: list[str] = []
    used = 0
    byte_limit = max(1, settings.embedding_max_input_bytes)
    for text in texts:
        size = len(json.dumps(text, ensure_ascii=True).encode("utf-8")) + 1
        if size > byte_limit:
            raise EmbeddingProviderError("A chunk exceeds the embedding input byte budget; rechunk the source")
        if batch and (len(batch) >= max(1, settings.embedding_max_inputs) or used + size > byte_limit):
            yield batch
            batch, used = [], 0
        batch.append(text)
        used += size
    if batch:
        yield batch


def _embed_with_openrouter(texts: list[str], *, guard: LedgerGuard | None = None) -> list[list[float]]:
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
    if guard is not None:
        bound = len(json.dumps(payload, ensure_ascii=True).encode("utf-8")) + 1024
        parsed = guard.invoke(
            role=INGESTION_EMBEDDING_OPERATION,
            provider="openrouter",
            model=settings.openrouter_embedding_model,
            operation=INGESTION_EMBEDDING_OPERATION,
            bounds={"max_input_tokens": bound},
            dispatch=lambda: _post_json(req, label="OpenRouter embeddings"),
            extract=lambda parsed: _openrouter_embedding_identity(
                parsed, pinned=settings.openrouter_embedding_model),
        )
    else:
        parsed = _post_json(req, label="OpenRouter embeddings")
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


def _openrouter_embedding_identity(parsed: dict, *, pinned: str) -> tuple[str | None, dict[str, int | None], Decimal | None]:
    """Return the returned model, billed units and any provider-reported cost.

    OpenRouter may return an embedding model without its provider prefix
    (``text-embedding-3-large`` for ``openai/text-embedding-3-large``); that is
    the same pinned model. Any other route is kept as-is so the guard rejects it.
    """
    usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
    prompt_tokens = usage.get("prompt_tokens")
    reported = usage.get("cost")
    reported_cost: Decimal | None = None
    if isinstance(reported, (int, float)) and not isinstance(reported, bool):
        reported_cost = Decimal(str(reported))
    returned = parsed.get("model") if isinstance(parsed.get("model"), str) else None
    if returned and (returned == pinned or pinned.endswith(f"/{returned}") or returned.endswith(f"/{pinned}")):
        returned = pinned
    return returned, {
        "input_tokens": prompt_tokens if isinstance(prompt_tokens, int)
        and not isinstance(prompt_tokens, bool) and prompt_tokens >= 0 else None,
        "cached_input_tokens": None,
        "output_tokens": 0,
        "cached_output_tokens": 0,
        "requests": 1,
    }, reported_cost


def _post_json(req: request.Request, *, label: str) -> dict:
    try:
        with request.urlopen(req, timeout=60.0) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:  # pragma: no cover - provider failure
        detail = exc.read().decode("utf-8", errors="replace")
        raise EmbeddingProviderError(f"{label} request failed with status {exc.code}: {detail}") from exc
    except error.URLError as exc:  # pragma: no cover - provider failure
        raise EmbeddingProviderError(f"{label} request failed: {exc.reason}") from exc
    return json.loads(raw) if raw else {}


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


class EmbeddingBatchSession:
    """Pre-warmed vectors shared by one batched ingestion run.

    Keys are the exact ordered text lists the per-source job will ask for, so
    the job reuses the batched provider calls instead of paying again.
    """

    def __init__(self) -> None:
        self._by_texts: dict[str, list[list[float]]] = {}

    @staticmethod
    def _key(texts: list[str]) -> str:
        return sha256(json.dumps(list(texts), ensure_ascii=False).encode("utf-8")).hexdigest()

    def lookup(self, texts: list[str]) -> list[list[float]] | None:
        return self._by_texts.get(self._key(texts))

    def store(self, texts: list[str], vectors: list[list[float]]) -> None:
        self._by_texts[self._key(texts)] = [list(vector) for vector in vectors]

    def forget(self, texts: list[str]) -> None:
        self._by_texts.pop(self._key(texts), None)


_BATCH_SESSION: ContextVar[EmbeddingBatchSession | None] = ContextVar("embedding_batch_session", default=None)


@contextmanager
def embedding_batch_session(session: EmbeddingBatchSession) -> Iterator[EmbeddingBatchSession]:
    token = _BATCH_SESSION.set(session)
    try:
        yield session
    finally:
        _BATCH_SESSION.reset(token)
