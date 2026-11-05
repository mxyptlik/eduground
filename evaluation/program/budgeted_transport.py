"""Per-attempt OpenRouter guard for Instructor/RAGAS async judge requests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx

from evaluation.program.budget import BudgetError, BudgetLedger
from evaluation.program.budgeted_call import _reported_openrouter_cost, maximum_call_cost
from evaluation.program.pricing import PriceRegistry, cost_call
from evaluation.program.usage import usage_from_result


class BudgetedOpenRouterTransport(httpx.AsyncBaseTransport):
    """Reserve before each actual HTTP attempt, including Instructor retries.

    This transport deliberately supports only non-streaming chat completions.
    It records metadata and usage, never prompt text, response bodies or keys.
    An unreadable/bill-unknown response blocks subsequent requests in the run.
    """

    def __init__(
        self, *, inner: httpx.AsyncBaseTransport, ledger: BudgetLedger,
        prices: PriceRegistry, model_id: str, role: str = "judge",
        max_input_tokens: int, max_output_tokens: int,
    ) -> None:
        self.inner = inner
        self.ledger = ledger
        self.prices = prices
        self.model_id = model_id
        self.role = role
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.attempts: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (request.method != "POST" or request.url.host != "openrouter.ai"
                or request.url.path != "/api/v1/chat/completions" or request.url.scheme != "https"):
            raise BudgetError("judge transport only permits OpenRouter HTTPS chat completions")
        try:
            payload = json.loads(await request.aread())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BudgetError("judge request is not JSON") from exc
        if (not isinstance(payload, dict) or payload.get("model") != self.model_id
                or payload.get("stream") is True or not isinstance(payload.get("messages"), list)):
            raise BudgetError("judge request changed model, streaming mode or messages")
        output_limit = payload.get("max_tokens", payload.get("max_completion_tokens"))
        if (isinstance(output_limit, bool) or not isinstance(output_limit, int)
                or output_limit < 1 or output_limit > self.max_output_tokens):
            raise BudgetError("judge request has no bounded output token limit")
        payload["usage"] = {"include": True}
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if self.max_input_tokens < len(encoded) + 1024:
            raise BudgetError("judge prompt exceeds the conservative input token bound")
        now = datetime.now(timezone.utc)
        schedule = self.prices.schedule_for("openrouter", self.model_id, self.role, now)
        if schedule is None or schedule.currency != "USD":
            raise BudgetError("dated USD judge price schedule is missing")
        reserved = maximum_call_cost(schedule, max_input_tokens=self.max_input_tokens,
                                     max_output_tokens=self.max_output_tokens)
        headers = dict(request.headers)
        headers.pop("content-length", None)
        guarded = httpx.Request(request.method, request.url, headers=headers,
                                content=encoded, extensions=request.extensions)
        call_id = uuid4().hex
        self.ledger.reserve(call_id, self.role, reserved)
        try:
            response = await self.inner.handle_async_request(guarded)
            body = await response.aread()
        except Exception:
            self.ledger.settle(call_id, None)
            self.attempts.append({"call_id": call_id, "status": "unknown_after_dispatch",
                                  "reserved_usd": str(reserved)})
            raise
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("judge response is not a JSON object")
            result = SimpleNamespace(gateway="openrouter", response_model=data.get("model"),
                                     usage=data.get("usage"))
            usage = usage_from_result(result, call_id=call_id, operation=self.role, occurred_at=now)
            cost = _reported_openrouter_cost(result, call_id) or cost_call(usage, self.prices)
            self.ledger.settle(call_id, cost.amount)
            record = {"call_id": call_id, "status": cost.status,
                      "http_status": response.status_code, "model": data.get("model"),
                      "request_id": data.get("id"), "reserved_usd": str(reserved),
                      "actual_usd": str(cost.amount) if cost.amount is not None else None,
                      "usage": data.get("usage")}
            self.attempts.append(record)
            if cost.amount is None:
                raise BudgetError("judge response cost is unknown; further calls are blocked")
            if cost.amount > reserved:
                raise BudgetError("judge response cost exceeded its reservation")
            if data.get("model") != self.model_id:
                raise BudgetError("judge response routed to a different model")
        except BudgetError:
            raise
        except Exception as exc:
            self.ledger.settle(call_id, None)
            self.attempts.append({"call_id": call_id, "status": "unknown_response",
                                  "http_status": response.status_code, "reserved_usd": str(reserved)})
            raise BudgetError("judge response lacks verifiable usage or model") from exc
        forwarding_headers = dict(response.headers)
        for name in ("content-encoding", "content-length", "transfer-encoding"):
            forwarding_headers.pop(name, None)
        return httpx.Response(response.status_code, headers=forwarding_headers, content=body,
                              request=guarded, extensions=response.extensions)

    async def aclose(self) -> None:
        await self.inner.aclose()
