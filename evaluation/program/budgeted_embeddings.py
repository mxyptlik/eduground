"""Per-request budget guard for OpenRouter query and relevancy embeddings."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from importlib import import_module
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Callable, Mapping
from urllib.parse import urlparse
from uuid import uuid4

from evaluation.program.budget import BudgetError, BudgetLedger
from evaluation.program.budgeted_call import _reported_openrouter_cost, maximum_call_cost
from evaluation.program.config import EvaluationSettings
from evaluation.program.pricing import CallUsage, PriceRegistry, cost_call


class BudgetedEmbeddingRequester:
    """Guard one actual OpenRouter embedding dispatch, without fallback."""

    def __init__(self, inner: Callable[..., dict[str, Any]], *, ledger: BudgetLedger,
                 prices: PriceRegistry, model_id: str, operation: str) -> None:
        self.inner = inner
        self.ledger = ledger
        self.prices = prices
        self.model_id = model_id
        self.operation = operation
        self.attempts: list[dict[str, Any]] = []

    def __call__(self, url: str, payload: dict[str, Any], headers: dict[str, str],
                 timeout_seconds: float, *, provider: str = "openrouter") -> dict[str, Any]:
        parsed = urlparse(url)
        if (provider != "openrouter" or parsed.scheme != "https" or parsed.hostname != "openrouter.ai"
                or parsed.path != "/api/v1/embeddings" or payload.get("model") != self.model_id
                or not isinstance(payload.get("input"), list)
                or not all(isinstance(text, str) and text for text in payload["input"])):
            raise BudgetError("embedding request changed endpoint, model or inputs")
        now = datetime.now(timezone.utc)
        schedule = self.prices.schedule_for("openrouter", self.model_id, self.operation, now)
        if schedule is None or schedule.currency != "USD":
            raise BudgetError("dated USD embedding price schedule is missing")
        max_input_tokens = len(json.dumps(payload, ensure_ascii=True).encode("utf-8")) + 1024
        reserved = maximum_call_cost(schedule, max_input_tokens=max_input_tokens,
                                     max_output_tokens=1)
        call_id = uuid4().hex
        self.ledger.reserve(call_id, self.operation, reserved)
        try:
            response = self.inner(url, payload, headers, timeout_seconds, provider=provider)
        except Exception:
            self.ledger.settle(call_id, None)
            self.attempts.append({"call_id": call_id, "status": "unknown_after_dispatch",
                                  "reserved_usd": str(reserved)})
            raise
        try:
            returned_model = response.get("model") if isinstance(response, dict) else None
            if (not isinstance(returned_model, str) or not returned_model
                    or not (returned_model == self.model_id or self.model_id.endswith(f"/{returned_model}")
                            or returned_model.endswith(f"/{self.model_id}"))):
                raise ValueError("embedding response changed or omitted its model")
            raw = response.get("usage")
            if not isinstance(raw, dict):
                raise ValueError("embedding response omitted usage")
            prompt_tokens = raw.get("prompt_tokens")
            if isinstance(prompt_tokens, bool) or not isinstance(prompt_tokens, int) or prompt_tokens < 0:
                raise ValueError("embedding response omitted prompt tokens")
            # The documented embeddings usage has only prompt/total tokens;
            # bill all prompt tokens at the standard rate, conservatively.
            usage = CallUsage(call_id=call_id, provider="openrouter", model=self.model_id,
                              operation=self.operation, occurred_at=now,
                              input_tokens=prompt_tokens, cached_input_tokens=0,
                              output_tokens=0, cached_output_tokens=0, requests=1)
            raw_result = SimpleNamespace(gateway="openrouter", response_model=self.model_id, usage=raw)
            cost = _reported_openrouter_cost(raw_result, call_id) or cost_call(usage, self.prices)
            self.ledger.settle(call_id, cost.amount)
            self.attempts.append({"call_id": call_id, "status": cost.status,
                                  "model": self.model_id, "request_id": response.get("id"),
                                  "reserved_usd": str(reserved),
                                  "actual_or_estimated_usd": str(cost.amount) if cost.amount is not None else None,
                                  "cost_basis": "provider_reported" if raw.get("cost") is not None else "dated_price_estimate",
                                  "prompt_tokens": prompt_tokens})
            if cost.amount is None:
                raise BudgetError("embedding cost is unknown; further calls are blocked")
            if cost.amount > reserved:
                raise BudgetError("embedding cost exceeded its reservation")
        except BudgetError:
            raise
        except Exception as exc:
            self.ledger.settle(call_id, None)
            self.attempts.append({"call_id": call_id, "status": "unknown_response",
                                  "reserved_usd": str(reserved)})
            raise BudgetError("embedding response lacks verifiable model or usage") from exc
        return response


def build_budgeted_embedding_provider(
    settings: EvaluationSettings, *, operation: str, ledger: BudgetLedger,
    prices: PriceRegistry, environ: Mapping[str, str] | None = None,
) -> tuple[Any, BudgetedEmbeddingRequester]:
    """Build a controlled OpenRouter embedding provider without a request."""
    if operation not in {"query_embedding", "relevance_embedding"}:
        raise BudgetError("unsupported controlled embedding operation")
    provider_name = (settings.embedding_provider if operation == "query_embedding"
                     else settings.relevance_embedding_provider)
    model_id = (settings.embedding_model if operation == "query_embedding"
                else settings.relevance_embedding_model)
    if provider_name != "openrouter" or not model_id:
        raise BudgetError("controlled embeddings currently require a pinned OpenRouter model")
    if settings.max_api_spend_usd is None or Decimal(str(settings.max_api_spend_usd)) != ledger.cap_usd:
        raise BudgetError("embedding ledger and settings must share the USD cap")
    source = os.environ if environ is None else environ
    key = source.get("CURRICULUM_TUTOR_OPENROUTER_API_KEY", "").strip()
    if not key:
        raise BudgetError("OpenRouter embedding key is missing")
    api_root = Path(__file__).resolve().parents[2] / "apps" / "api"
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    module = import_module("app.integrations.embeddings.base")
    requester = BudgetedEmbeddingRequester(module._request_json, ledger=ledger,
        prices=prices, model_id=model_id, operation=operation)
    provider = module.OpenRouterEmbeddingProvider(
        config=module.OpenRouterEmbeddingConfig(api_key=key, model=model_id),
        fallback=object(), requester=requester,
    )
    provider.fallback = None
    return provider, requester


def build_ragas_budgeted_embeddings(
    settings: EvaluationSettings, *, ledger: BudgetLedger, prices: PriceRegistry,
) -> tuple[Any, BudgetedEmbeddingRequester]:
    """Build a ragas ``BaseRagasEmbedding`` subclass over the guarded provider.

    The ragas collections metrics type-check their embeddings argument, so the
    adapter must subclass the modern base class; the import stays lazy so the
    collection pipeline never requires ragas to be installed.
    """
    from ragas.embeddings.base import BaseRagasEmbedding

    provider, requester = build_budgeted_embedding_provider(
        settings, operation="relevance_embedding", ledger=ledger, prices=prices,
    )

    class _BudgetedRagasEmbeddings(BaseRagasEmbedding):
        def embed_text(self, text: str, **kwargs: Any) -> list[float]:
            vectors = provider.embed([text])
            if not vectors or not isinstance(vectors[0], list):
                raise BudgetError("embedding provider returned no vector")
            return [float(value) for value in vectors[0]]

        async def aembed_text(self, text: str, **kwargs: Any) -> list[float]:
            import asyncio

            return await asyncio.to_thread(self.embed_text, text)

    return _BudgetedRagasEmbeddings(), requester
