"""Independent RAGAS 0.4.3 metric calls over frozen response snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from math import isfinite
from typing import Any


RAGAS_METRICS = frozenset({
    "faithfulness", "context_recall", "context_precision",
    "answer_relevancy", "factual_correctness_f1",
})


@dataclass(frozen=True)
class RagasOutcome:
    metric_id: str
    value: float | None
    status: str
    reason: str | None
    ragas_version: str | None


def _metric_class(metric_id: str):
    found = _version()
    if found != "0.4.3":
        raise RuntimeError(f"RAGAS 0.4.3 is required; found {found or 'not installed'}")
    from evaluation.ragas.ragas_compat import install_vertexai_import_compatibility
    install_vertexai_import_compatibility()
    from ragas.metrics.collections import (
        AnswerRelevancy, ContextPrecisionWithReference, ContextRecall,
        FactualCorrectness, Faithfulness,
    )
    return {
        "faithfulness": Faithfulness,
        "context_recall": ContextRecall,
        "context_precision": ContextPrecisionWithReference,
        "answer_relevancy": AnswerRelevancy,
        "factual_correctness_f1": FactualCorrectness,
    }[metric_id]


def _version() -> str | None:
    try:
        return version("ragas")
    except PackageNotFoundError:
        return None


async def score_ragas_metric(
    metric_id: str, *, llm: Any, user_input: str, response: str,
    retrieved_contexts: list[str] | None = None,
    reference: str | None = None,
    embeddings: Any | None = None,
    answerable: bool = True,
) -> RagasOutcome:
    """Score one requested metric; skipped cases are explicit N/A, not zero."""
    if metric_id not in RAGAS_METRICS:
        raise ValueError(f"Unknown RAGAS metric: {metric_id}")
    if not user_input.strip():
        raise ValueError("user_input is required")
    if not response.strip():
        raise ValueError("response is required")
    ragas_version = _version()

    def na(reason: str) -> RagasOutcome:
        return RagasOutcome(metric_id, None, "not_applicable", reason, ragas_version)

    if not answerable and metric_id in {"context_recall", "context_precision", "factual_correctness_f1"}:
        return na("unanswerable reference")
    if metric_id in {"faithfulness", "context_recall", "context_precision"} and not retrieved_contexts:
        return na("no retrieved contexts")
    if metric_id in {"context_recall", "context_precision", "factual_correctness_f1"} and not (reference or "").strip():
        return na("no reviewed reference")
    if metric_id == "answer_relevancy" and embeddings is None:
        raise ValueError("answer_relevancy requires pinned evaluation embeddings")

    cls = _metric_class(metric_id)
    if metric_id == "answer_relevancy":
        metric = cls(llm=llm, embeddings=embeddings)
        result = await metric.ascore(user_input=user_input, response=response)
    elif metric_id == "factual_correctness_f1":
        metric = cls(llm=llm, mode="f1")
        result = await metric.ascore(response=response, reference=reference)
    elif metric_id == "faithfulness":
        metric = cls(llm=llm)
        result = await metric.ascore(user_input=user_input, response=response,
                                     retrieved_contexts=retrieved_contexts)
    elif metric_id == "context_recall":
        metric = cls(llm=llm)
        result = await metric.ascore(user_input=user_input, reference=reference,
                                     retrieved_contexts=retrieved_contexts)
    else:
        metric = cls(llm=llm)
        result = await metric.ascore(user_input=user_input, reference=reference,
                                     retrieved_contexts=retrieved_contexts)
    try:
        value = float(result.value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"RAGAS returned a nonnumeric {metric_id} score") from exc
    if not isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"RAGAS returned an out-of-range {metric_id} score")
    return RagasOutcome(metric_id, value, "completed", None, ragas_version)
