from __future__ import annotations

import json

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


class OpenRouterRerankerProvider(RerankerProvider):
    def __init__(self) -> None:
        if not settings.openrouter_api_key:
            raise DependencyUnavailableError(
                code="openrouter_api_key_missing",
                message="CURRICULUM_TUTOR_OPENROUTER_API_KEY is required for reranking",
                provider="openrouter",
            )
        self._client = httpx.Client(timeout=60.0)

    def rerank(self, query: str, items: list[dict]) -> list[dict]:
        if not items:
            return []
        numbered_passages = [
            {
                "index": index,
                "source_id": item.get("source").id if item.get("source") is not None else item.get("source_id"),
                "chunk_id": item.get("chunk").id if item.get("chunk") is not None else item.get("chunk_id"),
                "text": (item.get("chunk").text if item.get("chunk") is not None else item.get("text", ""))[:3000],
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
            response = self._client.post(
                f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
                headers=_openrouter_headers(),
                json={
                    "model": settings.openrouter_rerank_model,
                    "messages": [
                        {"role": "system", "content": "You are a ranking engine. Return strict JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
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
            if not isinstance(index, int) or not (0 <= index < len(items)) or index in used_indices:
                continue
            used_indices.add(index)
            item = dict(items[index])
            item["rerank_score"] = float(score) if isinstance(score, (float, int)) else None
            item["rank_after"] = rank
            reranked.append(item)

        if len(reranked) < len(items):
            for index, item in enumerate(items):
                if index in used_indices:
                    continue
                passthrough = dict(item)
                passthrough["rerank_score"] = passthrough.get("rerank_score")
                passthrough["rank_after"] = len(reranked) + 1
                reranked.append(passthrough)
        return reranked


def _openrouter_headers() -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    if settings.openrouter_http_referer:
        headers["HTTP-Referer"] = settings.openrouter_http_referer
    if settings.openrouter_title:
        headers["X-Title"] = settings.openrouter_title
    return headers
