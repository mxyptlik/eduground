"""Normalize optional provider usage without inventing absent token counts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from evaluation.program.pricing import CallUsage


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _nested(data: dict[str, Any], key: str, field: str) -> int | None:
    details = data.get(key)
    return _integer(details.get(field)) if isinstance(details, dict) else None


def usage_from_result(
    result: Any, *, call_id: str, operation: str, occurred_at: datetime | None = None,
) -> CallUsage:
    """Convert `LLMCallResult` into a priced-call input.

    `response_model` is required; a gateway request with unknown routing is
    ineligible for a controlled run. Cached/reasoning fields remain null when
    omitted so the price calculator can surface incomplete billing evidence.
    """
    if result.gateway not in ("openrouter", "gemini"):
        raise ValueError("unsupported LLM gateway")
    if not isinstance(result.response_model, str) or not result.response_model.strip():
        raise ValueError("provider response did not identify its model")
    usage = result.usage if isinstance(result.usage, dict) else {}
    if result.gateway == "openrouter":
        completion = _integer(usage.get("completion_tokens"))
        reasoning = _nested(usage, "completion_tokens_details", "reasoning_tokens")
        if completion is not None and reasoning is not None and reasoning > completion:
            # Some OpenRouter upstreams report reasoning tokens outside
            # ``completion_tokens`` instead of inside it. The provider bills
            # both as generated output, so the billable total is their sum;
            # keeping the raw value would trip the subset guard in CallUsage.
            completion = completion + reasoning
        fields = {
            "input_tokens": _integer(usage.get("prompt_tokens")),
            "cached_input_tokens": _nested(usage, "prompt_tokens_details", "cached_tokens"),
            "output_tokens": completion,
            "cached_output_tokens": _nested(usage, "completion_tokens_details", "cached_tokens"),
            "reasoning_output_tokens": reasoning,
        }
    else:
        candidate_tokens = _integer(usage.get("candidatesTokenCount"))
        thought_tokens = _integer(usage.get("thoughtsTokenCount"))
        fields = {
            "input_tokens": _integer(usage.get("promptTokenCount")),
            "cached_input_tokens": _integer(usage.get("cachedContentTokenCount")),
            # Gemini reports visible candidate and thinking tokens separately;
            # both are billed as generated output. Missing thoughts metadata is
            # left unknown rather than silently undercounting a thinking model.
            "output_tokens": candidate_tokens + thought_tokens if candidate_tokens is not None and thought_tokens is not None else None,
            "cached_output_tokens": 0,
            "reasoning_output_tokens": thought_tokens,
        }
    return CallUsage(
        call_id=call_id,
        provider=result.gateway,
        model=result.response_model.removeprefix("models/") if result.gateway == "gemini" else result.response_model,
        operation=operation,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        requests=1,
        **fields,
    )
