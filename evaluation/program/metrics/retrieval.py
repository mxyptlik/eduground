"""Deterministic retrieval scores over reviewer-labelled evidence.

Each standalone relevant ID is one evidence requirement. IDs within an
alternative group are interchangeable ways to satisfy *one* requirement.
The ranked list retains its original positions: repeated IDs consume a rank
but only their first occurrence can receive credit.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log2
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class RetrievalScores:
    """Per-question scores. ``None`` means the metric is not applicable."""

    k: int
    recall_at_k: float | None
    precision_at_k: float | None
    hit_at_k: float | None
    reciprocal_rank: float | None
    ndcg_at_k: float | None
    retrieved_count: int
    relevant_requirement_count: int
    reason: str | None = None


def score_retrieval(
    ranked_ids: Sequence[str],
    *,
    k: int,
    relevant_ids: Iterable[str] = (),
    alternative_groups: Mapping[str, Iterable[str]] | None = None,
    relevance_grades: Mapping[str, float] | None = None,
    answerable: bool = True,
) -> RetrievalScores:
    """Score one ranked result against reviewed evidence IDs.

    ``relevant_ids`` are separate required evidence units. Each value in
    ``alternative_groups`` supplies IDs for one required unit. An ID cannot
    belong to two units. ``relevance_grades`` may assign nonnegative graded
    relevance to labelled IDs; omitted grades default to 1. A grade of zero
    means the ID is known irrelevant and receives no credit.

    Precision uses unique returned IDs in its denominator, matching the set
    definition in the programme scope. At most one ID per evidence unit can
    earn precision credit. Empty retrieval scores zero when positive labels
    exist. Missing positive labels, or ``answerable=False``, produce N/A.
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    if not isinstance(answerable, bool):
        raise TypeError("answerable must be a boolean")

    units: dict[str, tuple[str, ...]] = {}
    owner: dict[str, str] = {}
    standalone = tuple(relevant_ids)
    for evidence_id in standalone:
        _validate_id(evidence_id, "relevant ID")
        if evidence_id in owner:
            raise ValueError(f"duplicate relevant ID: {evidence_id}")
        unit = f"id:{evidence_id}"
        units[unit] = (evidence_id,)
        owner[evidence_id] = unit

    for group_name, options in (alternative_groups or {}).items():
        _validate_id(group_name, "alternative group name")
        option_ids = tuple(options)
        if not option_ids:
            raise ValueError(f"alternative group {group_name} is empty")
        unit = f"group:{group_name}"
        if unit in units:
            raise ValueError(f"duplicate alternative group: {group_name}")
        units[unit] = option_ids
        for evidence_id in option_ids:
            _validate_id(evidence_id, "alternative ID")
            if evidence_id in owner:
                raise ValueError(f"evidence ID belongs to multiple units: {evidence_id}")
            owner[evidence_id] = unit

    grades = {evidence_id: 1.0 for evidence_id in owner}
    for evidence_id, grade in (relevance_grades or {}).items():
        if evidence_id not in owner:
            raise ValueError(f"grade is not attached to a labelled ID: {evidence_id}")
        if isinstance(grade, bool) or not isinstance(grade, (int, float)) or not isfinite(grade) or grade < 0:
            raise ValueError(f"invalid relevance grade for {evidence_id}")
        grades[evidence_id] = float(grade)

    positive_units = {
        unit: ids for unit, ids in units.items() if any(grades[evidence_id] > 0 for evidence_id in ids)
    }
    if not answerable or not positive_units:
        return RetrievalScores(
            k=k,
            recall_at_k=None,
            precision_at_k=None,
            hit_at_k=None,
            reciprocal_rank=None,
            ndcg_at_k=None,
            retrieved_count=min(k, len(ranked_ids)),
            relevant_requirement_count=len(positive_units),
            reason="unanswerable" if not answerable else "no positive relevance labels",
        )

    seen_ids: set[str] = set()
    credited_units: set[str] = set()
    found_units: set[str] = set()
    unique_results: set[str] = set()
    relevant_unique_count = 0
    first_relevant_rank: int | None = None
    dcg = 0.0

    for rank, evidence_id in enumerate(ranked_ids[:k], start=1):
        _validate_id(evidence_id, "ranked ID")
        unique_results.add(evidence_id)
        if evidence_id in seen_ids:
            continue
        seen_ids.add(evidence_id)
        unit = owner.get(evidence_id)
        if unit is None or grades[evidence_id] == 0:
            continue
        found_units.add(unit)
        if first_relevant_rank is None:
            first_relevant_rank = rank
        if unit not in credited_units:
            credited_units.add(unit)
            relevant_unique_count += 1
            dcg += (2 ** grades[evidence_id] - 1) / log2(rank + 1)

    ideal_grades = sorted(
        (max(grades[evidence_id] for evidence_id in ids) for ids in positive_units.values()),
        reverse=True,
    )[:k]
    idcg = sum((2 ** grade - 1) / log2(rank + 1) for rank, grade in enumerate(ideal_grades, start=1))

    return RetrievalScores(
        k=k,
        recall_at_k=len(found_units) / len(positive_units),
        precision_at_k=relevant_unique_count / len(unique_results) if unique_results else 0.0,
        hit_at_k=float(bool(found_units)),
        reciprocal_rank=1 / first_relevant_rank if first_relevant_rank else 0.0,
        ndcg_at_k=dcg / idcg if idcg else None,
        retrieved_count=min(k, len(ranked_ids)),
        relevant_requirement_count=len(positive_units),
    )


def mean_reciprocal_rank(scores: Iterable[RetrievalScores]) -> float | None:
    """Macro MRR over applicable questions; N/A when none are applicable."""
    values = [score.reciprocal_rank for score in scores if score.reciprocal_rank is not None]
    return sum(values) / len(values) if values else None


def hit_rate(scores: Iterable[RetrievalScores]) -> float | None:
    """Share of applicable questions with at least one relevant top-k result."""
    values = [score.hit_at_k for score in scores if score.hit_at_k is not None]
    return sum(values) / len(values) if values else None


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
