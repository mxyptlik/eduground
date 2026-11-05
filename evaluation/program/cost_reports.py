"""Separate ingestion, serving and evaluation API expenses by attempt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal

from evaluation.program.pricing import CallCost, CallUsage, PriceRegistry, cost_call, sum_call_costs


Category = Literal["ingestion", "serving", "evaluation"]
Outcome = Literal["success", "failure"]


@dataclass(frozen=True)
class CostedAttempt:
    usage: CallUsage
    category: Category
    role: str
    course_id: str
    outcome: Outcome
    document_id: str | None = None

    def __post_init__(self) -> None:
        if self.category not in ("ingestion", "serving", "evaluation"):
            raise ValueError("invalid cost category")
        if self.outcome not in ("success", "failure"):
            raise ValueError("invalid attempt outcome")
        if not self.role.strip() or not self.course_id.strip():
            raise ValueError("role and course_id are required")


def _total(rows: list[tuple[CostedAttempt, CallCost]]) -> dict[str, Any]:
    amount = sum_call_costs([cost for _, cost in rows])
    return {
        "call_count": amount.call_count,
        "failed_call_count": sum(attempt.outcome == "failure" for attempt, _ in rows),
        "known_subtotal": str(amount.known_subtotal),
        "amount": None if amount.amount is None else str(amount.amount),
        "currency": amount.currency,
        "unknown_call_ids": list(amount.unknown_call_ids),
    }


def build_cost_report(attempts: Iterable[CostedAttempt], prices: PriceRegistry) -> dict[str, Any]:
    """Include failed/retried calls and preserve unknown costs in every group."""
    records = tuple(attempts)
    ids = [row.usage.call_id for row in records]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate call_id")
    costed = [(row, cost_call(row.usage, prices)) for row in records]
    categories = {category: _total([(row, cost) for row, cost in costed if row.category == category])
                  for category in ("ingestion", "serving", "evaluation")}
    role_keys = sorted({(row.category, row.role, row.usage.provider, row.usage.model) for row in records})
    roles = [
        {"category": category, "role": role, "provider": provider, "model": model,
         **_total([(row, cost) for row, cost in costed
                   if (row.category, row.role, row.usage.provider, row.usage.model) == key])}
        for key in role_keys
        for category, role, provider, model in [key]
    ]
    courses = {course_id: _total([(row, cost) for row, cost in costed if row.course_id == course_id])
               for course_id in sorted({row.course_id for row in records})}
    documents = {document_id: _total([(row, cost) for row, cost in costed if row.document_id == document_id])
                 for document_id in sorted({row.document_id for row in records if row.document_id})}
    return {
        "schema_version": 1,
        "actual_or_estimated": "estimated_from_recorded_usage_and_price_registry",
        "overall": _total(costed),
        "categories": categories,
        "roles": roles,
        "courses": courses,
        "documents": documents,
        "note": "Provider invoices or response-reported charges may differ; unknown cost is never counted as zero.",
    }
