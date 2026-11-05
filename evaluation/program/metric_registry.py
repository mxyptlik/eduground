"""Selectable, lazy metric entry points for the evaluation programme.

Only implemented metrics are registered. Planning a retrieval-only run does
not import RAGAS, provider adapters, pricing, or load-test code.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    version: str
    module: str
    function: str
    required_fields: tuple[str, ...]
    provider_calls_per_item: int = 0

    def load(self) -> Callable[..., Any]:
        result = getattr(import_module(self.module), self.function)
        if not callable(result):
            raise TypeError(f"metric {self.metric_id} is not callable")
        return result


_RETRIEVAL = "evaluation.program.metrics.retrieval"
METRICS: dict[str, MetricSpec] = {
    name: MetricSpec(
        metric_id=name,
        version="1.0",
        module=_RETRIEVAL,
        function="score_retrieval",
        required_fields=("ranked_chunk_ids", "reviewed_relevance_labels"),
    )
    for name in ("id_recall_at_k", "precision_at_k", "hit_at_k", "mrr", "ndcg_at_k")
}
for name, fields in {
    "faithfulness": ("user_input", "response", "retrieved_contexts"),
    "context_recall": ("user_input", "reference", "retrieved_contexts"),
    "context_precision": ("user_input", "reference", "retrieved_contexts"),
    "answer_relevancy": ("user_input", "response", "evaluation_embeddings"),
    "factual_correctness_f1": ("response", "reference"),
}.items():
    METRICS[name] = MetricSpec(name, "ragas-0.4.3", "evaluation.program.metrics.ragas", "score_ragas_metric", fields, 1)
_FIELDS = {
    "id_recall_at_k": "recall_at_k",
    "precision_at_k": "precision_at_k",
    "hit_at_k": "hit_at_k",
    "mrr": "reciprocal_rank",
    "ndcg_at_k": "ndcg_at_k",
}


def select_metrics(names: Iterable[str]) -> tuple[MetricSpec, ...]:
    requested = tuple(names)
    if not requested:
        raise ValueError("select at least one metric")
    if len(set(requested)) != len(requested):
        raise ValueError("duplicate metric selection")
    unknown = set(requested) - METRICS.keys()
    if unknown:
        raise ValueError(f"metrics are not implemented: {', '.join(sorted(unknown))}")
    return tuple(METRICS[name] for name in requested)


def score_selected(
    names: Iterable[str], ranked_ids: list[str], *, k: int,
    relevant_ids: Iterable[str] = (), alternative_groups: dict[str, Iterable[str]] | None = None,
    relevance_grades: dict[str, float] | None = None, answerable: bool = True,
) -> dict[str, dict[str, Any]]:
    """Compute selected deterministic fields with one shared retrieval pass."""
    selected = select_metrics(names)
    if any(spec.module != _RETRIEVAL for spec in selected):
        raise ValueError("score_selected accepts only deterministic retrieval metrics")
    score = selected[0].load()(
        ranked_ids, k=k, relevant_ids=relevant_ids,
        alternative_groups=alternative_groups,
        relevance_grades=relevance_grades, answerable=answerable,
    )
    return {
        spec.metric_id: {
            "version": spec.version,
            "value": getattr(score, _FIELDS[spec.metric_id]),
            "reason": score.reason,
        }
        for spec in selected
    }
