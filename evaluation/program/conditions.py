"""Pure, matched-context construction for controlled answer conditions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence


@dataclass(frozen=True)
class Candidate:
    chunk_id: str
    source_id: str
    text: str
    token_count: int
    initial_score: float
    rank_before: int
    locator: str | None = None
    source_version_id: str | None = None


@dataclass(frozen=True)
class ConditionContext:
    condition: str
    raw_candidate_ids: tuple[str, ...]
    final_chunk_ids: tuple[str, ...]
    exact_context: str
    omitted_chunk_ids: tuple[str, ...]
    rerank_status: str


@dataclass(frozen=True)
class RerankOrder:
    chunk_ids: tuple[str, ...]
    status: str = "succeeded"
    response_model: str | None = None
    request_id: str | None = None
    usage: dict | None = None
    truncated_passage_count: int | None = None
    ranked_coverage: float | None = None
    scores: tuple[float | None, ...] | None = None


def _validate_candidates(candidates: Sequence[Candidate]) -> None:
    ids = [candidate.chunk_id for candidate in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("candidate chunk IDs must be unique")
    if [candidate.rank_before for candidate in candidates] != list(range(1, len(candidates) + 1)):
        raise ValueError("candidates must retain the original contiguous ranking")
    if any(not c.chunk_id or not c.source_id or not c.text.strip() or c.token_count < 1 for c in candidates):
        raise ValueError("candidate source/text/token count is invalid")


def build_context(
    condition: str, raw_candidates: Sequence[Candidate], ranked_ids: Sequence[str], *,
    final_k: int, max_context_tokens: int, rerank_status: str,
) -> ConditionContext:
    """Use the same final count/token bound across ranking conditions."""
    if final_k < 1 or max_context_tokens < 1:
        raise ValueError("final_k and max_context_tokens must be positive")
    _validate_candidates(raw_candidates)
    by_id = {candidate.chunk_id: candidate for candidate in raw_candidates}
    if len(set(ranked_ids)) != len(ranked_ids) or set(ranked_ids) != set(by_id):
        raise ValueError("ranking must contain each original candidate exactly once")
    selected: list[Candidate] = []
    omitted: list[str] = []
    used_tokens = 0
    for chunk_id in ranked_ids:
        candidate = by_id[chunk_id]
        if len(selected) >= final_k or used_tokens + candidate.token_count > max_context_tokens:
            omitted.append(chunk_id)
            continue
        selected.append(candidate)
        used_tokens += candidate.token_count
    # The source label is part of the exact prompt context; preserve it in the
    # artifact so citation markers can later resolve to a specific chunk.
    context = "\n\n".join(
        f"[source:{candidate.source_id} chunk:{candidate.chunk_id}]\n{candidate.text}"
        for candidate in selected
    )
    return ConditionContext(condition, tuple(by_id), tuple(c.chunk_id for c in selected),
                            context, tuple(omitted), rerank_status)


def matched_contexts(
    raw_candidates: Sequence[Candidate], *,
    final_k: int, max_context_tokens: int,
    rerank: Callable[[Sequence[Candidate]], Sequence[str] | RerankOrder],
) -> tuple[ConditionContext, ConditionContext]:
    """Build controls from one frozen candidate list; raise on bad rerank output."""
    _validate_candidates(raw_candidates)
    raw_ids = [candidate.chunk_id for candidate in raw_candidates]
    control = build_context("no_rerank", raw_candidates, raw_ids,
        final_k=final_k, max_context_tokens=max_context_tokens, rerank_status="disabled")
    reranked = rerank(tuple(raw_candidates))
    if isinstance(reranked, RerankOrder):
        ranked_ids = list(reranked.chunk_ids)
        status = reranked.status
        if status not in {"succeeded", "partial"}:
            raise ValueError("reranker outcome must be succeeded or partial")
    else:
        ranked_ids = list(reranked)
        status = "succeeded"
    treatment = build_context("rerank", raw_candidates, ranked_ids,
        final_k=final_k, max_context_tokens=max_context_tokens, rerank_status=status)
    return control, treatment


def no_retrieval_context() -> ConditionContext:
    return ConditionContext("no_retrieval", (), (), "", (), "not_applicable")


def reviewed_evidence_context(evidence: Sequence[object]) -> ConditionContext:
    """Diagnostic context built only from reviewer-accepted evidence quotes.

    The quotes are the frozen evidence, not a fresh retrieval result; callers
    must not score retrieval metrics against this condition.
    """
    ids: list[str] = []
    parts: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        chunk_id = str(getattr(item, "chunk_id", ""))
        source_id = str(getattr(item, "source_id", ""))
        quote = str(getattr(item, "quote", ""))
        if not chunk_id or not source_id or not quote.strip():
            raise ValueError("reviewed evidence needs source, chunk and quote")
        ids.append(chunk_id)
        if chunk_id not in seen:
            seen.add(chunk_id)
            parts.append(f"[source:{source_id} chunk:{chunk_id}]\n{quote}")
    unique = tuple(dict.fromkeys(ids))
    return ConditionContext("reviewed_evidence", unique, unique, "\n\n".join(parts), (), "not_applicable")
