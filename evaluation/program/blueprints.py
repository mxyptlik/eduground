"""Offline, per-course authoring blueprints and coverage accounting."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from evaluation.program.authoring import AuthoringError, QuestionRequest, author_prompt


@dataclass(frozen=True)
class Blueprint:
    course_id: str
    batches: tuple[QuestionRequest, ...]


def parse_blueprint(payload: dict[str, Any], evidence_packet: dict[str, Any]) -> Blueprint:
    """Bind a manually chosen question mix to an exported course evidence packet.

    Chunk selection is explicit so a plan can be reviewed before any Kimi call.
    The authoring parser separately checks all returned source/quote claims.
    """
    if not isinstance(payload, dict) or set(payload) != {"course_id", "batches"}:
        raise AuthoringError("blueprint requires only course_id and batches")
    course_id = payload["course_id"]
    if not isinstance(course_id, str) or not course_id.strip() or course_id != evidence_packet.get("course_id"):
        raise AuthoringError("blueprint course differs from evidence packet")
    raw_batches = payload["batches"]
    if not isinstance(raw_batches, list) or not raw_batches:
        raise AuthoringError("blueprint needs at least one batch")
    batches: list[QuestionRequest] = []
    splits_by_chunk: dict[str, str] = {}
    for number, raw in enumerate(raw_batches, start=1):
        if not isinstance(raw, dict) or set(raw) != {"count", "question_type", "difficulty", "split", "chunk_ids"}:
            raise AuthoringError(f"batch {number} fields are invalid")
        chunk_ids = raw["chunk_ids"]
        if not isinstance(chunk_ids, list) or not all(isinstance(cid, str) and cid for cid in chunk_ids):
            raise AuthoringError(f"batch {number} chunk IDs must be non-empty strings")
        request = QuestionRequest(raw["count"], raw["question_type"], raw["difficulty"],
                                  raw["split"], tuple(chunk_ids))
        author_prompt(evidence_packet, request)  # Also verifies every selected chunk exists.
        for chunk_id in request.chunk_ids:
            prior = splits_by_chunk.get(chunk_id)
            if prior is not None and prior != request.split:
                raise AuthoringError(f"chunk {chunk_id} is shared across development and held_out batches")
            splits_by_chunk[chunk_id] = request.split
        batches.append(request)
    return Blueprint(course_id, tuple(batches))


def blueprint_plan(blueprint: Blueprint, evidence_packet: dict[str, Any]) -> dict[str, Any]:
    """Return a reproducible, provider-free preview and target distribution."""
    rows: list[dict[str, Any]] = []
    by_split: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    by_difficulty: Counter[str] = Counter()
    for number, request in enumerate(blueprint.batches, start=1):
        prompt, digest = author_prompt(evidence_packet, request)
        by_split[request.split] += request.count
        by_type[request.question_type] += request.count
        by_difficulty[request.difficulty] += request.count
        rows.append({"batch": number, "count": request.count,
                     "question_type": request.question_type, "difficulty": request.difficulty,
                     "split": request.split, "chunk_ids": list(request.chunk_ids),
                     "prompt_sha256": digest, "prompt_utf8_bytes": len(prompt.encode("utf-8"))})
    return {"course_id": blueprint.course_id, "total_requested": sum(by_split.values()),
            "targets_by_split": dict(sorted(by_split.items())),
            "targets_by_type": dict(sorted(by_type.items())),
            "targets_by_difficulty": dict(sorted(by_difficulty.items())),
            "batches": rows, "provider_calls_made": 0}
