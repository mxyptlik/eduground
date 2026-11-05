"""Offline adapters from Eduground retrieval records to auditable condition inputs."""

from __future__ import annotations

from math import isfinite
from typing import Any, Sequence

from evaluation.program.conditions import Candidate, ConditionContext, RerankOrder


NO_RETRIEVAL_SYSTEM_PROMPT = (
    "Answer the student's question using your general knowledge. "
    "If you do not know, say so. No documents or retrieval evidence are available."
)

CITATION_MARKER_INSTRUCTION = (
    " When a statement relies on the provided evidence, attach the exact evidence marker "
    "of the supporting passage inline right after that statement, for example [chunk:<chunk_id>]. "
    "Never invent a marker and never cite a passage that does not support the statement."
)


def snapshot_candidates(
    records: Sequence[Any], *, current_source_versions: dict[str, str] | None = None
) -> tuple[Candidate, ...]:
    """Reject stale, cross-source or non-current chunks before a run.

    ``current_source_versions=None`` records the snapshot without a version
    comparison; the caller must mark that run as unchecked rather than claim
    frozen-version verification.
    """
    snapshots: list[Candidate] = []
    for index, record in enumerate(records, start=1):
        chunk, source = record.chunk, record.source
        if record.rank_before != index or not isfinite(float(record.score)):
            raise ValueError("candidate ranking or score is invalid")
        if chunk.source_id != source.id:
            raise ValueError(f"candidate {chunk.id} source identity mismatch")
        if current_source_versions is not None and current_source_versions.get(source.id) != chunk.source_version_id:
            raise ValueError(f"candidate {chunk.id} is not from the frozen source version")
        locator = (f"slide {chunk.start_slide}" if chunk.start_slide is not None else
                   f"page {chunk.start_page}" if chunk.start_page is not None else None)
        snapshots.append(Candidate(
            chunk_id=chunk.id, source_id=source.id, text=chunk.text,
            token_count=chunk.token_count, initial_score=float(record.score),
            rank_before=index, locator=locator,
            source_version_id=getattr(chunk, "source_version_id", None),
        ))
    return tuple(snapshots)


def rerank_order(question: str, records: Sequence[Any], reranker: Any) -> RerankOrder:
    """Use existing LLM reranker but expose partial output instead of hiding it."""
    inputs = [{"chunk": record.chunk, "source": record.source, "score": record.score,
               "rank_before": record.rank_before, "rank_after": record.rank_after,
               "rerank_score": None} for record in records]
    if hasattr(reranker, "rerank_with_result"):
        result = reranker.rerank_with_result(question, inputs, strict=True)
        requested = reranker.config.model
        if result.response_model != requested:
            raise ValueError("reranker response did not confirm its pinned model")
        output = result.items
        response_model = result.response_model
        request_id = result.request_id
        usage = result.usage
        truncated = getattr(result, "truncated_passage_count", None)
        coverage = getattr(result, "ranked_coverage", None)
    else:
        output = reranker.rerank(question, inputs)
        response_model = request_id = usage = None
        truncated = coverage = None
    ids = tuple(item["chunk"].id for item in output)
    scores = tuple(float(item["rerank_score"]) if item.get("rerank_score") is not None else None for item in output)
    original = {record.chunk.id for record in records}
    if len(ids) != len(original) or set(ids) != original:
        raise ValueError("reranker omitted, duplicated or invented a candidate")
    status = "succeeded" if all(item.get("rerank_score") is not None for item in output) else "partial"
    return RerankOrder(ids, status, response_model, request_id, usage, truncated, coverage, scores)


def prompt_messages(condition: ConditionContext, *, question: str, grounded_system_prompt: str) -> list[dict[str, str]]:
    """Build and retain exact zero-history model input for one question."""
    if not question.strip():
        raise ValueError("question is required")
    if condition.condition == "no_retrieval":
        return [{"role": "system", "content": NO_RETRIEVAL_SYSTEM_PROMPT},
                {"role": "user", "content": question}]
    if condition.condition not in {"no_rerank", "rerank", "reviewed_evidence"}:
        raise ValueError("unknown answer condition")
    if not grounded_system_prompt.strip():
        raise ValueError("grounded system prompt is required")
    # For an empty history this is the same two-message shape produced by
    # chat_runtime.build_prompt_messages, without requiring LangChain merely
    # to plan and inspect a candidate prompt offline.
    from app.services.chat_runtime import format_grounded_user_message
    return [{"role": "system", "content": grounded_system_prompt},
            {"role": "user", "content": format_grounded_user_message(
                context=condition.exact_context, question=question)}]
