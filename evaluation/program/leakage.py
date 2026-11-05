"""Cross-course duplicate and leakage disposition report (M1.7).

This report never deletes or decides: exact duplicate files are listed with
their courses, near-duplicate candidates and shared question/evidence material
are flagged ``requires_human_disposition`` so the reviewer can exclude or
accept them explicitly.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from evaluation.program.contracts import CourseDataset


_NORMALIZE_NAME = re.compile(r"[^a-z0-9]+")


def _name_key(path: str) -> str:
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return _NORMALIZE_NAME.sub("", stem.casefold())


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]{3,}", value.casefold())}


def _similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def cross_course_leakage_report(
    inventory: Mapping[str, Any], datasets: Sequence[CourseDataset] = (), *,
    near_duplicate_threshold: float = 0.8, size_tolerance: float = 0.05,
    question_similarity_threshold: float = 0.9,
) -> dict[str, Any]:
    """Combine a read-only course inventory with draft datasets."""
    documents = list(inventory.get("documents") or [])
    exact_groups = list(inventory.get("duplicates") or [])
    exact_paths = {path for group in exact_groups for path in group["paths"]}

    near_duplicates: list[dict[str, Any]] = []
    for index, left in enumerate(documents):
        for right in documents[index + 1:]:
            if left["relative_path"] in exact_paths and right["relative_path"] in exact_paths \
                    and left["sha256"] == right["sha256"]:
                continue
            if left["suffix"] != right["suffix"]:
                continue
            if _name_key(left["relative_path"]) != _name_key(right["relative_path"]):
                continue
            smaller, larger = sorted((left["byte_size"], right["byte_size"]))
            if larger == 0 or (larger - smaller) / larger > size_tolerance:
                continue
            near_duplicates.append({
                "left": left["relative_path"], "right": right["relative_path"],
                "courses": sorted({left["course_id"], right["course_id"]}),
                "reason": "same normalised filename and comparable size but different bytes",
                "status": "requires_human_disposition",
            })

    question_overlap: list[dict[str, Any]] = []
    shared_chunks: list[dict[str, Any]] = []
    chunk_owner: dict[str, str] = {}
    chunk_question: dict[str, tuple[str, str]] = {}
    for dataset in datasets:
        for sample in dataset.samples:
            for evidence in sample.evidence:
                owner = chunk_owner.get(evidence.chunk_id)
                if owner is not None and owner != dataset.course_id:
                    shared_chunks.append({
                        "chunk_id": evidence.chunk_id,
                        "courses": sorted({owner, dataset.course_id}),
                        "status": "requires_human_disposition",
                        "reason": "the same chunk ID is evidence in two courses",
                    })
                else:
                    chunk_owner[evidence.chunk_id] = dataset.course_id
                chunk_question[evidence.chunk_id] = (dataset.course_id, sample.sample_id)
    for left in datasets:
        for right in datasets:
            if left.course_id >= right.course_id:
                continue
            for left_sample in left.samples:
                for right_sample in right.samples:
                    if _similarity(left_sample.question, right_sample.question) >= question_similarity_threshold:
                        question_overlap.append({
                            "left": {"course_id": left.course_id, "sample_id": left_sample.sample_id,
                                     "question": left_sample.question},
                            "right": {"course_id": right.course_id, "sample_id": right_sample.sample_id,
                                      "question": right_sample.question},
                            "similarity": round(_similarity(left_sample.question, right_sample.question), 4),
                            "status": "requires_human_disposition",
                        })

    cross_course_exact = [group for group in exact_groups if len(group["course_ids"]) > 1]
    return {
        "schema_version": 1,
        "report_only": True,
        "thresholds": {
            "near_duplicate_similarity": near_duplicate_threshold,
            "size_tolerance": size_tolerance,
            "question_similarity": question_similarity_threshold,
        },
        "exact_duplicate_groups": exact_groups,
        "cross_course_duplicate_groups": cross_course_exact,
        "near_duplicate_candidates": near_duplicates,
        "cross_course_question_overlap": question_overlap,
        "shared_evidence_chunks": shared_chunks,
        "summary": {
            "exact_duplicate_groups": len(exact_groups),
            "cross_course_duplicate_groups": len(cross_course_exact),
            "near_duplicate_candidates": len(near_duplicates),
            "cross_course_question_pairs": len(question_overlap),
            "shared_evidence_chunks": len(shared_chunks),
            "disposition_required": bool(
                cross_course_exact or near_duplicates or question_overlap or shared_chunks),
        },
        "note": "No file or question was removed; every flagged row needs an explicit human disposition.",
    }
