"""Score a completed collection run: deterministic retrieval, citations, RAGAS.

Scoring reads only the immutable run journal and the frozen dataset. Metric
steps are journalled per item/condition/metric with input hashes, so a failed
or interrupted scoring pass can resume without re-calling the judge. Missing
answers, N/A cases and judge failures stay distinct and are never converted to
zero or silently dropped from denominators.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable, Mapping, Sequence

from evaluation.program.artifacts import RunStore, StepKey
from evaluation.program.contracts import CourseDataset, Question
from evaluation.program.metrics.citations import CitationLink, CitationTarget, validate_citation_targets
from evaluation.program.metrics.ragas import RAGAS_METRICS
from evaluation.program.metrics.retrieval import score_retrieval
from evaluation.program.runner import RETRIEVAL_CONDITIONS, RunnerError


_MARKER = re.compile(r"\[chunk:([^\]\s]+)\]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

RETRIEVAL_METRIC_IDS = ("id_recall_at_k", "precision_at_k", "hit_at_k", "mrr", "ndcg_at_k")


def _item_id(course_id: str, sample_id: str) -> str:
    return f"{course_id}:{sample_id}"


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


def completed_answers(store: RunStore, dataset: CourseDataset, conditions: Sequence[str]) -> dict[tuple[str, str], dict]:
    """Completed answer results only; failed/missing/missing steps are absent."""
    results: dict[tuple[str, str], dict] = {}
    for sample in dataset.samples:
        item = _item_id(dataset.course_id, sample.sample_id)
        for condition in conditions:
            record = store.get_step(StepKey("answer", item, condition))
            if record is not None and record.status == "completed" and isinstance(record.result, dict):
                results[(sample.sample_id, condition)] = record.result
    return results


def ranked_chunk_ids(result: Mapping[str, Any]) -> list[str]:
    """Effective ranking: post-rerank order when it succeeded, else original order."""
    candidates = list(result.get("candidates") or [])
    if result.get("rerank_status") in {"succeeded", "partial"}:
        ordered = sorted(
            (row for row in candidates if row.get("rank_after") is not None),
            key=lambda row: row["rank_after"],
        )
        if len(ordered) == len(candidates):
            return [str(row["chunk_id"]) for row in ordered]
    return [str(row["chunk_id"]) for row in sorted(candidates, key=lambda row: row["rank_before"])]


def context_texts(result: Mapping[str, Any]) -> list[str]:
    """Exact evidence texts sent to the answer model, in final context order."""
    by_id = {str(row["chunk_id"]): row for row in result.get("candidates") or []}
    texts: list[str] = []
    for chunk_id in result.get("final_chunk_ids") or []:
        row = by_id.get(str(chunk_id))
        if row is not None and str(row.get("text", "")).strip():
            texts.append(str(row["text"]))
    return texts


@dataclass(frozen=True)
class RetrievalRow:
    item_id: str
    condition: str
    k: int
    metric_id: str
    value: float | None
    reason: str | None
    relevance_source: str


def score_retrieval_for_run(
    dataset: CourseDataset, store: RunStore, *, conditions: Sequence[str], k_values: Sequence[int],
    relevance_labels: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[RetrievalRow]:
    """Deterministic R01-R05 per item/condition from the frozen journal.

    Without explicit human relevance labels the frozen evidence chunk IDs are a
    minimum-relevance annotation, and rows say so; exhaustive Recall@k/nDCG
    claims require the labels file.
    """
    answers = completed_answers(store, dataset, conditions)
    rows: list[RetrievalRow] = []
    for sample in dataset.samples:
        labels = (relevance_labels or {}).get(sample.sample_id) or {}
        relevant = labels.get("relevant_ids")
        if relevant is None:
            relevant = list(dict.fromkeys(evidence.chunk_id for evidence in sample.evidence))
            source = "frozen_evidence_minimum"
        else:
            source = "reviewer_labels"
        for condition in conditions:
            if condition not in RETRIEVAL_CONDITIONS:
                continue
            result = answers.get((sample.sample_id, condition))
            if result is None:
                continue
            ranked = ranked_chunk_ids(result)
            for k in k_values:
                score = score_retrieval(
                    ranked, k=k, relevant_ids=relevant,
                    alternative_groups=labels.get("alternative_groups"),
                    relevance_grades=labels.get("relevance_grades"),
                    answerable=sample.answerable,
                )
                for metric_id, value, reason in (
                    ("id_recall_at_k", score.recall_at_k, score.reason),
                    ("precision_at_k", score.precision_at_k, score.reason),
                    ("hit_at_k", score.hit_at_k, score.reason),
                    ("mrr", score.reciprocal_rank, score.reason),
                    ("ndcg_at_k", score.ndcg_at_k, score.reason),
                ):
                    rows.append(RetrievalRow(_item_id(dataset.course_id, sample.sample_id),
                                             condition, k, metric_id, value, reason, source))
    return rows


@dataclass(frozen=True)
class CitationRow:
    item_id: str
    condition: str
    claim_id: str
    marker: str
    status: str
    reason: str | None


def citation_checks_for_run(
    dataset: CourseDataset, store: RunStore, *, conditions: Sequence[str],
) -> list[CitationRow]:
    """Deterministic C01-style target checks for explicit ``[chunk:id]`` markers.

    Retrieval cards alone never count as links: only markers actually present in
    the answer are checked. Semantic claim support remains a separate judge task.
    """
    answers = completed_answers(store, dataset, conditions)
    rows: list[CitationRow] = []
    for sample in dataset.samples:
        item = _item_id(dataset.course_id, sample.sample_id)
        for condition in conditions:
            result = answers.get((sample.sample_id, condition))
            if result is None:
                continue
            answer_text = str(result.get("answer_text") or "")
            known_targets = {
                str(row["chunk_id"]): CitationTarget(str(row["chunk_id"]), str(row["source_id"]),
                                                     row.get("locator"))
                for row in result.get("candidates") or []
            }
            context_ids = {str(chunk_id) for chunk_id in result.get("final_chunk_ids") or []}
            links: list[CitationLink] = []
            for sentence_index, sentence in enumerate(_SENTENCE_SPLIT.split(answer_text), start=1):
                for marker in _MARKER.findall(sentence):
                    target = known_targets.get(marker)
                    links.append(CitationLink(
                        claim_id=f"{item}:s{sentence_index}", marker=f"[chunk:{marker}]",
                        chunk_id=marker, source_id=target.source_id if target else "unknown",
                    ))
            for check in validate_citation_targets(links, context_chunk_ids=context_ids, known_targets=known_targets):
                rows.append(CitationRow(item, condition, check.claim_id, check.marker, check.status, check.reason))
    return rows


@dataclass(frozen=True)
class RagasRow:
    item_id: str
    condition: str
    metric_id: str
    status: str
    value: float | None
    reason: str | None


def _ragas_required_fields(metric_id: str, sample: Question, result: Mapping[str, Any]) -> dict[str, Any] | None:
    """Inputs for one metric, or None when the case is N/A without a judge call."""
    if not str(result.get("answer_text") or "").strip():
        return None
    contexts = context_texts(result)
    inputs: dict[str, Any] = {
        "user_input": sample.question,
        "response": str(result["answer_text"]),
        "retrieved_contexts": contexts,
        "reference": sample.reference_answer,
        "answerable": sample.answerable,
    }
    if metric_id in {"faithfulness", "context_recall", "context_precision"} and not contexts:
        return None
    if metric_id in {"context_recall", "context_precision", "factual_correctness_f1"} and not (
            sample.reference_answer or "").strip():
        return None
    return inputs


def score_ragas_for_run(
    dataset: CourseDataset, store: RunStore, *, conditions: Sequence[str], metric_ids: Sequence[str],
    judge_llm: Any, embeddings: Any | None = None, limit: int | None = None,
    recover_interrupted: bool = False, retry_failed: bool = False, concurrency: int = 2,
) -> list[RagasRow]:
    """Journalled RAGAS scoring over completed answers.

    The judge is supplied already budget-guarded by the caller. Non-completed
    answers produce explicit rows with status ``no_answer`` rather than being
    dropped. ``limit`` bounds the number of new judge calls in this invocation;
    a previously failed metric step is only re-dispatched with ``retry_failed``.
    """
    unknown = set(metric_ids) - RAGAS_METRICS
    if unknown:
        raise RunnerError(f"unknown RAGAS metrics: {', '.join(sorted(unknown))}")
    answers = completed_answers(store, dataset, conditions)
    rows: list[RagasRow] = []
    pending: list[tuple[Question, str, str, dict[str, Any]]] = []
    for sample in dataset.samples:
        item = _item_id(dataset.course_id, sample.sample_id)
        for condition in conditions:
            result = answers.get((sample.sample_id, condition))
            for metric_id in metric_ids:
                if result is None:
                    rows.append(RagasRow(item, condition, metric_id, "no_answer", None,
                                         "the answer step did not complete"))
                    continue
                inputs = _ragas_required_fields(metric_id, sample, result)
                if inputs is None:
                    rows.append(RagasRow(item, condition, metric_id, "not_applicable", None,
                                         "metric inputs are not applicable for this item/condition"))
                    continue
                key = StepKey("metric", item, f"{condition}::{metric_id}")
                input_hash = _hash({"metric": metric_id, "answer": inputs["response"],
                                    "contexts": inputs["retrieved_contexts"],
                                    "reference": inputs["reference"]})
                record = store.get_step(key)
                if record is not None and record.status == "completed" and record.input_hash == input_hash:
                    stored = record.result or {}
                    rows.append(RagasRow(item, condition, metric_id, stored.get("status", "completed"),
                                         stored.get("value"), stored.get("reason")))
                    continue
                if record is not None and record.status == "running" and not recover_interrupted:
                    raise RunnerError(f"interrupted metric step {item}/{metric_id}; rerun with recovery")
                if record is not None and record.status == "failed" and not retry_failed:
                    rows.append(RagasRow(item, condition, metric_id, "failed", None,
                                         record.error_code or "judge_error"))
                    continue
                pending.append((sample, condition, metric_id, {"key": key, "hash": input_hash, **inputs}))

    if limit is not None and limit < 0:
        raise RunnerError("limit must be nonnegative")
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 16:
        raise RunnerError("concurrency must be between 1 and 16")
    budget_limited = pending if limit is None else pending[:limit]

    async def run_all() -> None:
        semaphore = asyncio.Semaphore(concurrency)

        async def one(entry: tuple[Question, str, str, dict[str, Any]]) -> None:
            sample, condition, metric_id, payload = entry
            item = _item_id(dataset.course_id, sample.sample_id)
            key, input_hash = payload["key"], payload["hash"]
            async with semaphore:
                record = store.get_step(key)
                begin_kwargs: dict[str, bool] = {}
                if record is not None and record.status == "failed":
                    begin_kwargs["retry_failed"] = True
                elif record is not None and record.status == "running":
                    begin_kwargs["recover_interrupted"] = True
                started = store.begin_step(key, input_hash=input_hash, **begin_kwargs)
                try:
                    from evaluation.program.metrics.ragas import score_ragas_metric

                    outcome = await score_ragas_metric(
                        metric_id, llm=judge_llm, embeddings=embeddings,
                        user_input=payload["user_input"],
                        response=payload["response"], retrieved_contexts=payload["retrieved_contexts"],
                        reference=payload["reference"], answerable=payload["answerable"],
                    )
                    store.complete_step(key, input_hash=input_hash, attempt=started.attempts,
                                        result={"status": outcome.status, "value": outcome.value,
                                                "reason": outcome.reason,
                                                "ragas_version": outcome.ragas_version})
                    rows.append(RagasRow(item, condition, metric_id, outcome.status, outcome.value, outcome.reason))
                except Exception as exc:
                    store.fail_step(key, input_hash=input_hash, attempt=started.attempts,
                                    error_code="judge_error")
                    rows.append(RagasRow(item, condition, metric_id, "failed", None, type(exc).__name__))

        await asyncio.gather(*(one(entry) for entry in budget_limited))

    asyncio.run(run_all())
    if limit is not None and len(pending) > len(budget_limited):
        for sample, condition, metric_id, _ in pending[len(budget_limited):]:
            rows.append(RagasRow(_item_id(dataset.course_id, sample.sample_id), condition, metric_id,
                                 "deferred_limit", None, "judge-call limit reached in this invocation"))
    return rows
