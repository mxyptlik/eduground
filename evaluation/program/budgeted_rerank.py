"""Budgeted OpenRouter HTTP client for the existing synchronous reranker."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json as jsonlib
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from evaluation.program.budget import BudgetError, BudgetLedger
from evaluation.program.budgeted_call import _reported_openrouter_cost, maximum_call_cost
from evaluation.program.config import EvaluationSettings
from evaluation.program.pricing import PriceRegistry, cost_call
from evaluation.program.providers import build_reranker_provider
from evaluation.program.usage import usage_from_result


class BudgetedRerankClient:
    """Duck-typed `.post` wrapper; every provider HTTP attempt is reserved."""

    def __init__(self, inner: Any, *, ledger: BudgetLedger, prices: PriceRegistry,
                 model_id: str, max_input_tokens: int, max_output_tokens: int) -> None:
        self.inner = inner
        self.ledger = ledger
        self.prices = prices
        self.model_id = model_id
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.attempts: list[dict[str, Any]] = []

    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> Any:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname != "openrouter.ai"
                or parsed.path != "/api/v1/chat/completions"):
            raise BudgetError("reranker only permits OpenRouter HTTPS chat completions")
        if (json.get("model") != self.model_id or json.get("usage") != {"include": True}
                or json.get("stream") is True or not isinstance(json.get("messages"), list)):
            raise BudgetError("rerank request changed model, usage or messages")
        output_limit = json.get("max_tokens")
        if (isinstance(output_limit, bool) or not isinstance(output_limit, int)
                or not 1 <= output_limit <= self.max_output_tokens):
            raise BudgetError("rerank request has no bounded output token limit")
        encoded = jsonlib.dumps(json, ensure_ascii=True).encode("utf-8")
        if self.max_input_tokens < len(encoded) + 1024:
            raise BudgetError("rerank prompt exceeds conservative input token bound")
        now = datetime.now(timezone.utc)
        schedule = self.prices.schedule_for("openrouter", self.model_id, "rerank", now)
        if schedule is None or schedule.currency != "USD":
            raise BudgetError("dated USD reranker price schedule is missing")
        reserved = maximum_call_cost(schedule, max_input_tokens=self.max_input_tokens,
                                     max_output_tokens=self.max_output_tokens)
        call_id = uuid4().hex
        self.ledger.reserve(call_id, "rerank", reserved)
        try:
            response = self.inner.post(url, headers=headers, json=json)
        except Exception:
            self.ledger.settle(call_id, None)
            self.attempts.append({"call_id": call_id, "status": "unknown_after_dispatch",
                                  "reserved_usd": str(reserved)})
            raise
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("reranker response is not a JSON object")
            result = SimpleNamespace(gateway="openrouter", response_model=data.get("model"),
                                     usage=data.get("usage"))
            usage = usage_from_result(result, call_id=call_id, operation="rerank", occurred_at=now)
            cost = _reported_openrouter_cost(result, call_id) or cost_call(usage, self.prices)
            self.ledger.settle(call_id, cost.amount)
            self.attempts.append({"call_id": call_id, "status": cost.status,
                                  "http_status": response.status_code,
                                  "model": data.get("model"), "request_id": data.get("id"),
                                  "reserved_usd": str(reserved),
                                  "actual_usd": str(cost.amount) if cost.amount is not None else None,
                                  "usage": data.get("usage")})
            if cost.amount is None:
                raise BudgetError("reranker response cost is unknown; further calls are blocked")
            if cost.amount > reserved:
                raise BudgetError("reranker cost exceeded its reservation")
            if data.get("model") != self.model_id:
                raise BudgetError("reranker response routed to a different model")
        except BudgetError:
            raise
        except Exception as exc:
            self.ledger.settle(call_id, None)
            self.attempts.append({"call_id": call_id, "status": "unknown_response",
                                  "reserved_usd": str(reserved)})
            raise BudgetError("reranker response lacks verifiable usage or model") from exc
        return response

    def close(self) -> None:
        self.inner.close()


def build_budgeted_reranker(
    settings: EvaluationSettings, *, ledger: BudgetLedger, prices: PriceRegistry,
    max_input_tokens: int, environ: dict[str, str] | None = None,
) -> Any:
    """Construct pinned reranker with guarded HTTP; performs no network call."""
    if settings.max_api_spend_usd is None or Decimal(str(settings.max_api_spend_usd)) != ledger.cap_usd:
        raise BudgetError("reranker ledger and settings must share the USD cap")
    provider = build_reranker_provider(settings, environ=environ)
    provider._client = BudgetedRerankClient(
        provider._client, ledger=ledger, prices=prices,
        model_id=provider.config.model, max_input_tokens=max_input_tokens,
        max_output_tokens=settings.reranker_max_output_tokens,
    )
    return provider
