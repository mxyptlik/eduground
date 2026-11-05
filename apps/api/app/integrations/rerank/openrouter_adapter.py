from __future__ import annotations

import json
from dataclasses import dataclass
from math import isfinite

import httpx

from app.core.config import settings
from app.core.errors import DependencyUnavailableError, ProviderRequestError
from app.core.observability import traced_operation
from app.integrations.rerank.base import RerankerProvider


class RerankerProviderError(ProviderRequestError):
    def __init__(self, message: str, *, status_code: int = 502, retryable: bool = True, details: dict | None = None) -> None:
        super().__init__(
            code="openrouter_rerank_error",
            message=message,
            status_code=status_code,
            retryable=retryable,
            provider="openrouter",
            details=details or {},
        )


@dataclass(slots=True)
class OpenRouterRerankerConfig:
    api_key: str | None = None
    model: str = "openai/gpt-4.1-mini"
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 60.0
    max_passage_chars: int = 3000
    site_url: str | None = None
    app_name: str | None = None
    include_usage: bool = False
    max_output_tokens: int | None = None

    @classmethod
    def from_env(cls) -> "OpenRouterRerankerConfig":
        return cls(api_key=settings.openrouter_api_key, model=settings.openrouter_rerank_model,
                   base_url=settings.openrouter_base_url,
                   site_url=settings.openrouter_http_referer,
                   app_name=settings.openrouter_title)


@dataclass(slots=True)
class RerankCallResult:
    items: list[dict]
    requested_model: str
    response_model: str | None
    request_id: str | None
    usage: dict | None
    truncated_passage_count: int
    ranked_coverage: float


class OpenRouterRerankerProvider(RerankerProvider):
    def __init__(self, config: OpenRouterRerankerConfig | None = None, *, client: httpx.Client | None = None) -> None:
        self.config = config or OpenRouterRerankerConfig.from_env()
        if not self.config.api_key:
            raise DependencyUnavailableError(
                code="openrouter_api_key_missing",
                message="CURRICULUM_TUTOR_OPENROUTER_API_KEY is required for reranking",
                provider="openrouter",
            )
        if not self.config.model.strip() or self.config.max_passage_chars < 1:
            raise ValueError("reranker model and passage limit are required")
        self._client = client or httpx.Client(timeout=self.config.timeout_seconds)

    def rerank(self, query: str, items: list[dict]) -> list[dict]:
        return self.rerank_with_result(query, items).items

    def rerank_with_result(self, query: str, items: list[dict], *, strict: bool = False) -> RerankCallResult:
        if not items:
            return RerankCallResult([], self.config.model, None, None, None, 0, 1.0)
        passage_texts = [item.get("chunk").text if item.get("chunk") is not None else item.get("text", "")
                         for item in items]
        numbered_passages = [
            {
                "index": index,
                "source_id": item.get("source").id if item.get("source") is not None else item.get("source_id"),
                "chunk_id": item.get("chunk").id if item.get("chunk") is not None else item.get("chunk_id"),
                "text": passage_texts[index][:self.config.max_passage_chars],
            }
            for index, item in enumerate(items)
        ]
        prompt = (
            "Rank the provided passages by how useful they are for answering the query. "
            "Return strict JSON only in the form "
            '{"ranked_indices":[{"index":0,"score":0.95}]}. '
            "Scores must be between 0 and 1.\n\n"
            f"Query: {query}\n\nPassages:\n{json.dumps(numbered_passages, ensure_ascii=True)}"
        )
        with traced_operation(
            "provider.openrouter.rerank.request",
            metric_name="eduground_provider_call",
            metric_labels={"provider": "openrouter", "operation": "rerank"},
        ):
            payload = {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": "You are a ranking engine. Return strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }
            if self.config.include_usage:
                payload["usage"] = {"include": True}
            if self.config.max_output_tokens is not None:
                if self.config.max_output_tokens < 1:
                    raise ValueError("max_output_tokens must be positive")
                payload["max_tokens"] = self.config.max_output_tokens
            response = self._client.post(
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers=_openrouter_headers(self.config),
                json=payload,
            )
        if response.status_code >= 400:
            raise RerankerProviderError(
                f"OpenRouter rerank request failed: {response.status_code} {response.text}",
                status_code=502 if response.status_code >= 500 else 424,
                retryable=response.status_code >= 500 or response.status_code == 429,
                details={"upstream_status_code": response.status_code},
            )
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RerankerProviderError("OpenRouter rerank response was malformed") from exc
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RerankerProviderError(f"OpenRouter rerank response was not valid JSON: {content}") from exc

        ranked_entries = parsed.get("ranked_indices")
        if not isinstance(ranked_entries, list):
            raise RerankerProviderError("OpenRouter rerank response missing ranked_indices")

        reranked: list[dict] = []
        used_indices: set[int] = set()
        for rank, entry in enumerate(ranked_entries, start=1):
            if not isinstance(entry, dict):
                continue
            index = entry.get("index")
            score = entry.get("score")
            if isinstance(index, bool) or not isinstance(index, int) or not (0 <= index < len(items)) or index in used_indices:
                if strict:
                    raise RerankerProviderError("Reranker returned an invalid or repeated index")
                continue
            if isinstance(score, bool) or (score is not None and (
                    not isinstance(score, (float, int)) or not isfinite(score) or not 0 <= score <= 1)):
                if strict:
                    raise RerankerProviderError("Reranker returned an invalid score")
                # A NaN/out-of-range score is dropped, never stored: JSON's
                # non-standard NaN constant parses as a float in Python.
                score = None
            used_indices.add(index)
            item = dict(items[index])
            item["rerank_score"] = float(score) if score is not None else None
            item["rank_after"] = rank
            reranked.append(item)

        coverage = len(reranked) / len(items)
        if strict and coverage < 1:
            raise RerankerProviderError("Reranker omitted candidates in strict mode")
        if len(reranked) < len(items):
            for index, item in enumerate(items):
                if index in used_indices:
                    continue
                passthrough = dict(item)
                passthrough["rerank_score"] = passthrough.get("rerank_score")
                passthrough["rank_after"] = len(reranked) + 1
                reranked.append(passthrough)
        return RerankCallResult(
            items=reranked, requested_model=self.config.model,
            response_model=payload.get("model") if isinstance(payload.get("model"), str) else None,
            request_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
            usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else None,
            truncated_passage_count=sum(len(text) > self.config.max_passage_chars for text in passage_texts),
            ranked_coverage=coverage,
        )


def _openrouter_headers(config: OpenRouterRerankerConfig) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    if config.site_url:
        headers["HTTP-Referer"] = config.site_url
    if config.app_name:
        headers["X-Title"] = config.app_name
    return headers
