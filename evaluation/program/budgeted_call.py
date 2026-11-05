"""Budgeted non-streaming Gemini/OpenRouter role calls for controlled runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any
from uuid import uuid4

from evaluation.program.budget import BudgetError, BudgetLedger
from evaluation.program.config import EvaluationSettings
from evaluation.program.pricing import CallCost, CallUsage, PriceRegistry, PriceSchedule, cost_call
from evaluation.program.providers import RoleName, require_result_matches_role
from evaluation.program.usage import usage_from_result


@dataclass(frozen=True)
class BudgetedCall:
    result: Any
    usage: CallUsage
    cost: CallCost
    reserved_usd: Decimal


def maximum_call_cost(
    schedule: PriceSchedule, *, max_input_tokens: int, max_output_tokens: int,
) -> Decimal:
    """Conservatively price all known token/cache possibilities and one request."""
    if isinstance(max_input_tokens, bool) or max_input_tokens < 0 or isinstance(max_output_tokens, bool) or max_output_tokens < 1:
        raise BudgetError("positive output and nonnegative input token bounds are required")
    rates = {price.unit: price for price in schedule.prices}
    unsupported = set(rates) - {"input_tokens", "cached_input_tokens", "output_tokens", "cached_output_tokens", "requests"}
    if unsupported:
        raise BudgetError(f"cannot bound price units: {', '.join(sorted(unsupported))}")

    def rate(unit: str) -> Decimal:
        price = rates.get(unit)
        return Decimal(0) if price is None else price.amount / Decimal(price.quantity)

    if not rates.keys() & {"input_tokens", "cached_input_tokens", "output_tokens", "cached_output_tokens", "requests"}:
        raise BudgetError("price schedule has no boundable units")
    return (Decimal(max_input_tokens) * max(rate("input_tokens"), rate("cached_input_tokens"))
            + Decimal(max_output_tokens) * max(rate("output_tokens"), rate("cached_output_tokens"))
            + rate("requests"))


def _reported_openrouter_cost(result: Any, call_id: str) -> CallCost | None:
    if getattr(result, "gateway", None) != "openrouter" or not isinstance(getattr(result, "usage", None), dict):
        return None
    raw = result.usage.get("cost")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        amount = Decimal(str(raw))
    except Exception as exc:
        raise BudgetError("OpenRouter reported a malformed usage.cost") from exc
    if not amount.is_finite() or amount < 0:
        raise BudgetError("OpenRouter reported a malformed usage.cost")
    return CallCost(call_id, amount, amount, "USD", None, "OpenRouter response usage.cost", ())


def invoke_budgeted_role(
    settings: EvaluationSettings, role_name: RoleName, provider: Any,
    messages: list[dict[str, str]], *, ledger: BudgetLedger, prices: PriceRegistry,
    max_input_tokens: int, max_output_tokens: int,
) -> BudgetedCall:
    """Reserve before dispatch, settle every attempt, and verify returned routing.

    The caller must supply a conservative input-token ceiling; an unsupported
    price unit or unknown actual usage halts further calls through the ledger.
    This path is for one non-retrying adapter request. RAGAS/Instructor and
    reranker transports need their own per-attempt wrappers before live use.
    """
    if not hasattr(provider, "generate_messages_with_result"):
        raise BudgetError("provider lacks structured response telemetry")
    if settings.max_api_spend_usd is None or Decimal(str(settings.max_api_spend_usd)) != ledger.cap_usd:
        raise BudgetError("run settings and budget ledger must share the explicit USD cap")
    minimum_input_bound = max(
        len(json.dumps(messages, ensure_ascii=False).encode("utf-8")),
        len(json.dumps(messages, ensure_ascii=True).encode("utf-8")),
    ) + 1024
    if max_input_tokens < minimum_input_bound:
        raise BudgetError("input-token reservation is smaller than the conservative prompt bound")
    configured_output_limit = getattr(getattr(provider, "config", None), "max_output_tokens", None)
    if isinstance(configured_output_limit, int) and max_output_tokens < configured_output_limit:
        raise BudgetError("output-token reservation is smaller than provider configuration")
    role = settings.roles()[role_name]
    now = datetime.now(timezone.utc)
    schedule = prices.schedule_for(role.provider, role.model_id, role_name, now)
    if schedule is None or schedule.currency != "USD":
        raise BudgetError(f"dated USD price schedule missing for {role_name}")
    maximum = maximum_call_cost(schedule, max_input_tokens=max_input_tokens,
                                max_output_tokens=max_output_tokens)
    call_id = uuid4().hex
    ledger.reserve(call_id, role_name, maximum)
    try:
        result = provider.generate_messages_with_result(messages)
    except Exception:
        # A failed request may still be billed. Keep the reservation and block
        # dispatch until its actual cost is reconciled.
        ledger.settle(call_id, None)
        raise
    try:
        usage = usage_from_result(result, call_id=call_id, operation=role_name, occurred_at=now)
        cost = _reported_openrouter_cost(result, call_id) or cost_call(usage, prices)
    except Exception:
        ledger.settle(call_id, None)
        raise
    state = ledger.settle(call_id, cost.amount)
    if cost.amount is None:
        raise BudgetError(f"actual {role_name} cost is unknown; further calls are blocked")
    if cost.amount > maximum:
        raise BudgetError("provider cost exceeded the reserved per-call bound")
    if state.committed_usd > state.cap_usd:
        raise BudgetError("provider cost exceeded the reserved bound and run cap")
    require_result_matches_role(settings, role_name, result)
    return BudgetedCall(result, usage, cost, maximum)
