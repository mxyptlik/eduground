"""Heuristic review flags for duplicate draft questions and split leakage."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from evaluation.program.contracts import CourseDataset


@dataclass(frozen=True)
class DraftFlag:
    kind: str
    first_sample_id: str
    second_sample_id: str
    similarity: float | None
    blocks_freeze: bool


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / len(a | b) if a or b else 1.0


def draft_leakage_report(
    dataset: CourseDataset, evidence_packet: dict[str, Any], *,
    question_threshold: float = 0.8, chunk_threshold: float = 0.8,
) -> dict[str, Any]:
    """Flag pairs; exact shared evidence across splits blocks freezing.

    Jaccard similarity is a review heuristic, not a semantic duplicate judge.
    Every flagged pair requires human disposition before a final dataset freeze.
    """
    if not 0 < question_threshold <= 1 or not 0 < chunk_threshold <= 1:
        raise ValueError("similarity thresholds must be in (0, 1]")
    if evidence_packet.get("course_id") != dataset.course_id:
        raise ValueError("evidence packet course differs from dataset")
    chunks: dict[str, str] = {}
    for source in evidence_packet.get("sources", []):
        for chunk in source.get("chunks", []):
            if chunk["chunk_id"] in chunks:
                raise ValueError("duplicate chunk ID in evidence packet")
            chunks[chunk["chunk_id"]] = chunk["text"]
    for sample in dataset.samples:
        for evidence in sample.evidence:
            if evidence.chunk_id not in chunks:
                raise ValueError(f"draft {sample.sample_id} refers to missing chunk")
    flags: list[DraftFlag] = []
    samples = dataset.samples
    for i, first in enumerate(samples):
        for second in samples[i + 1:]:
            similarity = _similarity(first.question, second.question)
            if first.question.strip().casefold() == second.question.strip().casefold():
                flags.append(DraftFlag("exact_question", first.sample_id, second.sample_id, 1.0, True))
            elif similarity >= question_threshold:
                flags.append(DraftFlag("similar_question", first.sample_id, second.sample_id,
                                       round(similarity, 4), False))
            if first.split == second.split:
                continue
            first_ids = {e.chunk_id for e in first.evidence}
            second_ids = {e.chunk_id for e in second.evidence}
            if first_ids & second_ids:
                flags.append(DraftFlag("shared_chunk_across_splits", first.sample_id,
                                       second.sample_id, None, True))
                continue
            largest = max((_similarity(chunks[a], chunks[b]) for a in first_ids for b in second_ids),
                          default=0.0)
            if largest >= chunk_threshold:
                flags.append(DraftFlag("overlapping_chunk_text_across_splits", first.sample_id,
                                       second.sample_id, round(largest, 4), False))
    return {"course_id": dataset.course_id, "sample_count": len(samples),
            "question_threshold": question_threshold, "chunk_threshold": chunk_threshold,
            "flags": [flag.__dict__ for flag in flags],
            "blocking_count": sum(flag.blocks_freeze for flag in flags),
            "human_disposition_required": bool(flags)}
