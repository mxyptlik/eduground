"""Validate local review bundles and freeze human-accepted source-bound items.

The browser exports a self-reported review record. Hash checks detect accidental
edits and bind each decision to a revision; they do not authenticate a person.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Protocol

from evaluation.program.contracts import CourseDataset, adapt_legacy_v1


KIND = "eduground-human-review-v1"
RELEASE_KIND = "eduground-reviewed-release-v2"
DECISIONS = {"Pending", "Accept", "Flag", "Reject"}


class ReviewError(ValueError):
    pass


class EvidenceResolver(Protocol):
    def verify_source(self, manifest: dict[str, Any]) -> None: ...
    def chunk(self, chunk_id: str) -> tuple[str, str]:
        """Return (source_id, complete text) for an indexed chunk."""


def js_digest(value: Any) -> str:
    """Hash JSON using the browser's compact JSON.stringify representation.

    The dataset schema contains ordinary object keys and no non-finite floats.
    This matches the current review workspace export; v2 should use a formal
    canonical JSON format for broader numeric/dynamic-key support.
    """
    packed = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return sha256(packed.encode("utf-8")).hexdigest()


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _time(text: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewError("Review timestamp is invalid") from exc
    if dt.tzinfo is None:
        raise ReviewError("Review timestamp must include UTC offset")
    return dt


def _review_content_hash(review: dict[str, Any]) -> str:
    content = {key: review[key] for key in ("question", "reference_answer", "supporting_passages")}
    if "supporting_evidence" in review:
        content["supporting_evidence"] = review["supporting_evidence"]
    return js_digest(content)


def validate_packet(packet: dict[str, Any]) -> None:
    if not isinstance(packet, dict) or not isinstance(packet.get("dataset"), dict):
        raise ReviewError("Review packet lacks original dataset")
    dataset = packet["dataset"]
    samples = dataset.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ReviewError("Original dataset has no samples")
    ids = [row.get("sample_id") for row in samples if isinstance(row, dict)]
    if len(ids) != len(samples) or any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)):
        raise ReviewError("Original sample IDs are invalid or duplicated")
    if packet.get("id") != js_digest(dataset) or packet.get("dataset_hash") != packet["id"]:
        raise ReviewError("Original dataset hash does not match the review packet")
    if not isinstance(packet.get("course_id"), str) or not packet["course_id"].strip():
        raise ReviewError("Course ID is required")
    if dataset.get("schema_version") == 2 and dataset.get("course_id") != packet["course_id"]:
        raise ReviewError("Course ID differs from the original v2 dataset")
    reviews = packet.get("reviews")
    history = packet.get("history")
    if not isinstance(reviews, list) or not isinstance(history, list):
        raise ReviewError("Reviews and history must be arrays")
    sample_by_id = {row["sample_id"]: row for row in samples}
    for review in [*reviews, *history]:
        if not isinstance(review, dict) or review.get("sample_id") not in ids:
            raise ReviewError("Review references an unknown sample")
        if review.get("decision") not in DECISIONS:
            raise ReviewError("Review has an invalid decision")
        if not all(isinstance(review.get(k), str) for k in ("question", "reference_answer", "supporting_passages", "required_changes", "reviewer")):
            raise ReviewError("Review text fields are malformed")
        if not review["reviewer"].strip():
            raise ReviewError("Reviewer name is required")
        if not isinstance(review.get("revision"), int) or isinstance(review["revision"], bool) or review["revision"] < 1:
            raise ReviewError("Review revision is invalid")
        if not isinstance(review.get("evidence_checked"), bool):
            raise ReviewError("Evidence confirmation must be boolean")
        _time(review.get("reviewed_at_utc"))
        if review.get("content_hash") != _review_content_hash(review):
            raise ReviewError("Review content hash does not match the decision")
        original = sample_by_id[review["sample_id"]]
        answerable = original.get("answerable", True) if dataset.get("schema_version") == 2 else True
        if dataset.get("schema_version") == 2 and "supporting_evidence" in review:
            corrected = review["supporting_evidence"]
            evidence = original.get("evidence", [])
            if (not isinstance(corrected, list) or len(corrected) != len(evidence)
                    or any(not isinstance(item, dict)
                           or item.get("source_id") != source.get("source_id")
                           or item.get("chunk_id") != source.get("chunk_id")
                           or not isinstance(item.get("quote"), str) or not item["quote"].strip()
                           for item, source in zip(corrected, evidence, strict=True))):
                raise ReviewError("Review evidence must retain original source and chunk IDs")
            if review["supporting_passages"] != "\n\n".join(item["quote"] for item in corrected):
                raise ReviewError("Displayed passages differ from source-bound evidence")
        if not answerable and (review["supporting_passages"].strip() or review.get("supporting_evidence")):
            raise ReviewError("Unanswerable review cannot carry supporting evidence")
        if review["decision"] == "Accept" and (not review["evidence_checked"] or not review["question"].strip()
            or (answerable and (not review["reference_answer"].strip() or not review["supporting_passages"].strip()))):
            raise ReviewError("Accepted review lacks evidence confirmation or content")
        if review["decision"] in {"Flag", "Reject"} and not review["required_changes"].strip():
            raise ReviewError("Flagged or rejected review needs a reason")
    seen: set[str] = set()
    for review in reviews:
        if review["sample_id"] in seen:
            raise ReviewError("Multiple current reviews for one sample")
        seen.add(review["sample_id"])
        matching = [event for event in history if event["sample_id"] == review["sample_id"]]
        if not matching or review != max(matching, key=lambda event: event["revision"]):
            raise ReviewError("Current review must match the latest review-history revision")
    for sample_id in ids:
        events = sorted((event for event in history if event["sample_id"] == sample_id), key=lambda event: event["revision"])
        if [event["revision"] for event in events] != list(range(1, len(events) + 1)):
            raise ReviewError("Review history revisions must be complete and consecutive")
        if events and sample_id not in seen:
            raise ReviewError("Review history has no current decision")


def validate_bundle(bundle: dict[str, Any]) -> None:
    if not isinstance(bundle, dict) or bundle.get("kind") != KIND or not isinstance(bundle.get("packets"), list):
        raise ReviewError("Not an Eduground review bundle")
    seen: set[str] = set()
    for packet in bundle["packets"]:
        validate_packet(packet)
        if packet["id"] in seen:
            raise ReviewError("Duplicate review packet")
        seen.add(packet["id"])


def _passages(text: str) -> list[str]:
    # The v1 reviewer stores one passage per blank-line-separated block.
    return [part.strip() for part in text.replace("\r\n", "\n").split("\n\n") if part.strip()]


@dataclass(frozen=True)
class Release:
    dataset: CourseDataset
    provenance: dict[str, Any]
    counts: dict[str, int]


def release_packet(packet: dict[str, Any], resolver: EvidenceResolver) -> Release:
    validate_packet(packet)
    legacy = adapt_legacy_v1(packet["dataset"], course_id=packet["course_id"])
    source_by_id = {source.source_id: source for source in legacy.source_manifest}
    for source in packet["dataset"]["source_manifest"]:
        resolver.verify_source(source)
    reviews = {review["sample_id"]: review for review in packet["reviews"]}
    counts: Counter[str] = Counter()
    accepted: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for original in legacy.samples:
        review = reviews.get(original.sample_id)
        decision = review["decision"] if review else "Pending"
        counts[decision] += 1
        if decision != "Accept":
            continue
        assert review is not None
        if "supporting_evidence" in review:
            passages = [item["quote"] for item in review["supporting_evidence"]]
        elif original.answerable:
            passages = _passages(review["supporting_passages"])
        else:
            passages = []
        if len(passages) != len(original.evidence):
            raise ReviewError(f"{original.sample_id}: passage count changed; cannot map evidence to chunks")
        evidence: list[dict[str, Any]] = []
        for source_evidence, passage in zip(original.evidence, passages, strict=True):
            source_id, chunk_text = resolver.chunk(source_evidence.chunk_id)
            if source_id != source_evidence.source_id or source_id not in source_by_id:
                raise ReviewError(f"{original.sample_id}: evidence points at the wrong source")
            if _normalise(passage) not in _normalise(chunk_text):
                raise ReviewError(f"{original.sample_id}: corrected passage is not contained in its indexed chunk")
            evidence.append({**source_evidence.model_dump(), "quote": passage})
        accepted.append({
            **original.model_dump(exclude={"integrity", "question", "reference_answer", "evidence"}),
            "question": review["question"],
            "reference_answer": review["reference_answer"],
            "evidence": evidence,
            "integrity": "verified",
        })
        provenance.append({
            "sample_id": original.sample_id,
            "reviewer": review["reviewer"],
            "reviewed_at_utc": review["reviewed_at_utc"],
            "revision": review["revision"],
            "review_content_hash": review["content_hash"],
        })
    if not accepted:
        raise ReviewError("No human-accepted questions are eligible for release")
    release_data = CourseDataset.model_validate({
        **legacy.model_dump(exclude={"samples"}),
        "version": f"{legacy.version}+human-reviewed",
        "samples": accepted,
    })
    return Release(dataset=release_data,
                   provenance={"review_kind": KIND, "original_dataset_hash": packet["dataset_hash"],
                               "course_id": packet["course_id"], "review_records": provenance,
                               "identity_assurance": "reviewer self-reported in local workspace"},
                   counts={name: counts[name] for name in ("Pending", "Accept", "Flag", "Reject")})


def write_release(release: Release, destination: Path) -> Path:
    """Publish a pair of JSON files under a new, non-overwritable directory."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(prefix=".review-release-", dir=destination.parent) as temp:
        staging = Path(temp) / "release"
        staging.mkdir()
        dataset_json = json.dumps(release.dataset.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
        with (staging / "dataset.json").open("w", encoding="utf-8", newline="\n") as output:
            output.write(dataset_json)
        (staging / "dataset.sha256").write_text(sha256(dataset_json.encode("utf-8")).hexdigest() + "\n", encoding="ascii")
        (staging / "provenance.json").write_text(json.dumps({**release.provenance, "counts": release.counts}, indent=2) + "\n", encoding="utf-8")
        os.rename(staging, destination)
    return destination
