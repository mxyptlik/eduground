"""Read-only execution plan over reviewed course datasets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from evaluation.program.config import EvaluationSettings, configuration_hash
from evaluation.program.contracts import CourseDataset, adapt_legacy_v1
from evaluation.program.metric_registry import select_metrics


CONDITIONS = frozenset({"no_retrieval", "no_rerank", "rerank", "reviewed_evidence"})


def plan_evaluation(
    dataset_paths: Iterable[str | Path], *, metric_ids: Iterable[str],
    conditions: Iterable[str], settings: EvaluationSettings,
) -> dict[str, Any]:
    """Validate inputs and estimate call counts without creating a run or making calls.

    One answer call per item/condition is a lower bound. Judge calls and costs
    are explicitly unknown until RAGAS metric adapters and dated prices exist.
    """
    paths = tuple(Path(path) for path in dataset_paths)
    if not paths or len(set(path.resolve() for path in paths)) != len(paths):
        raise ValueError("provide unique dataset paths")
    selected = select_metrics(metric_ids)
    chosen = tuple(conditions)
    if not chosen or len(set(chosen)) != len(chosen) or set(chosen) - CONDITIONS:
        raise ValueError("provide unique known conditions")
    datasets: list[CourseDataset] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"dataset must be an object: {path}")
        dataset = adapt_legacy_v1(payload, course_id=payload.get("course_id") or path.stem)
        datasets.append(dataset)
    course_ids = [dataset.course_id for dataset in datasets]
    if len(set(course_ids)) != len(course_ids):
        raise ValueError("duplicate course_id among datasets")
    issues: list[str] = []
    for dataset in datasets:
        unchecked = sum(sample.integrity != "verified" for sample in dataset.samples)
        if unchecked:
            issues.append(f"{dataset.course_id}: {unchecked} items lack verified source integrity")
    for spec in selected:
        if "ranked_chunk_ids" in spec.required_fields and chosen == ("no_retrieval",):
            issues.append(f"{spec.metric_id}: retrieval scores are N/A in no_retrieval condition")
    if "rerank" in chosen and not settings.reranker_model:
        issues.append("rerank condition requires a pinned reranker model (EDUGROUND_EVAL_RERANKER_MODEL)")
    try:
        settings.preflight(
            require_paid_calls=True,
            require_ingestion_embedding=bool(set(chosen) - {"no_retrieval", "reviewed_evidence"}),
            require_relevance_embedding=any(spec.metric_id == "answer_relevancy" for spec in selected),
        )
    except ValueError as exc:
        issues.append(str(exc))
    item_count = sum(len(dataset.samples) for dataset in datasets)
    answer_calls_lower_bound = item_count * len(chosen)
    judge_calls_lower_bound = (
        sum(spec.provider_calls_per_item for spec in selected) * item_count * len(chosen)
    )
    reranker_calls_lower_bound = item_count if "rerank" in chosen else 0
    input_checks_passed = not issues
    return {
        "plan_version": "1.1",
        "plan_only": True,
        "configuration_hash": configuration_hash(settings.public_snapshot()),
        "courses": [
            {"course_id": dataset.course_id, "dataset_id": dataset.dataset_id,
             "item_count": len(dataset.samples), "source_count": len(dataset.source_manifest)}
            for dataset in datasets
        ],
        "course_count": len(datasets),
        "item_count": item_count,
        "conditions": list(chosen),
        "metrics": [{"id": spec.metric_id, "version": spec.version} for spec in selected],
        "collector": "evaluation.program.runner v1",
        "estimated_external_calls": {
            "answer_lower_bound": answer_calls_lower_bound,
            "judge_lower_bound": judge_calls_lower_bound,
            "reranker_lower_bound": reranker_calls_lower_bound,
            "note": "Retries, authoring, ingestion, judge prompts and token usage are not estimated here.",
        },
        "estimated_cost_usd": None,
        "cost_status": "unknown until a dated price registry and usage bounds are supplied",
        "max_api_spend_usd": settings.max_api_spend_usd,
        "input_checks_passed": input_checks_passed,
        "execution_ready": input_checks_passed,
        "issues": issues,
    }
