"""Source-bound Kimi draft authoring contracts and validation."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Sequence

from evaluation.program.contracts import CourseDataset, Question
from evaluation.program.model_output import ModelOutputError, validate_model_json


class AuthoringError(ValueError):
    pass


@dataclass(frozen=True)
class QuestionRequest:
    count: int
    question_type: str
    difficulty: str
    split: str
    chunk_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.count, int) or isinstance(self.count, bool) or not 1 <= self.count <= 10:
            raise AuthoringError("a Kimi batch must request 1–10 questions")
        if self.difficulty not in {"easy", "medium", "hard"}:
            raise AuthoringError("difficulty must be easy, medium or hard")
        if self.split not in {"development", "held_out"}:
            raise AuthoringError("invalid dataset split")
        if (not isinstance(self.question_type, str) or not self.question_type.strip()
                or not self.chunk_ids or len(set(self.chunk_ids)) != len(self.chunk_ids)):
            raise AuthoringError("question type and unique supporting chunk IDs are required")


def _chunks(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for source in packet["sources"]:
        for chunk in source["chunks"]:
            if chunk["chunk_id"] in result:
                raise AuthoringError("duplicate chunk ID in evidence packet")
            result[chunk["chunk_id"]] = {**chunk, "source_id": source["source_id"],
                                         "source_label": source["source_label"]}
    return result


def author_prompt(packet: dict[str, Any], request: QuestionRequest) -> tuple[str, str]:
    """Return source-only prompt and its SHA-256 hash for run provenance."""
    available = _chunks(packet)
    try:
        chosen = [available[chunk_id] for chunk_id in request.chunk_ids]
    except KeyError as exc:
        raise AuthoringError(f"unknown authoring chunk: {exc.args[0]}") from exc
    excerpts = [
        f"Source {chunk['source_label']} | source_id={chunk['source_id']} | chunk_id={chunk['chunk_id']}\n"
        f"Locator: page={chunk.get('start_page')} slide={chunk.get('start_slide')}\n"
        f"{chunk['text']}"
        for chunk in chosen
    ]
    prompt = (
        "You are Kimi authoring candidate questions for independent human review. "
        "Use only the supplied indexed evidence. Do not assume outside facts or invent quotes. "
        f"Return at most {request.count} distinct {request.difficulty} questions of type {request.question_type}. "
        "A question may require multiple passages. Each evidence quote must be an exact continuous span of its chunk. "
        "Return strict JSON only: {\"items\":[{\"question\":\"...\",\"reference_answer\":\"...\","
        "\"evidence\":[{\"source_id\":\"...\",\"chunk_id\":\"...\",\"quote\":\"...\"}]}]}. "
        "If evidence is insufficient, return fewer items or an empty list.\n\n"
        + "\n\n---\n\n".join(excerpts)
    )
    return prompt, sha256(prompt.encode("utf-8")).hexdigest()


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def parse_kimi_drafts(
    packet: dict[str, Any], request: QuestionRequest, response_text: str,
) -> tuple[list[Question], dict[str, Any]]:
    """Reject hallucinated sources/quotes and return a deficit, never filler."""
    try:
        payload = validate_model_json(response_text, label="Kimi output",
                                      expect="object", required_keys=("items",))
    except ModelOutputError as exc:
        raise AuthoringError(str(exc)) from exc
    if set(payload) != {"items"} or not isinstance(payload["items"], list):
        raise AuthoringError("Kimi output must contain only an items array")
    if len(payload["items"]) > request.count:
        raise AuthoringError("Kimi returned more questions than requested")
    available = _chunks(packet)
    permitted = set(request.chunk_ids)
    questions: list[Question] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload["items"], start=1):
        if not isinstance(raw, dict) or set(raw) != {"question", "reference_answer", "evidence"}:
            raise AuthoringError(f"item {index} has invalid fields")
        question = raw["question"]
        answer = raw["reference_answer"]
        evidence = raw["evidence"]
        if not isinstance(question, str) or not isinstance(answer, str):
            raise AuthoringError(f"item {index} has an invalid question or reference")
        # Models occasionally wrap the question in straight/typographic quotes
        # or use a full-width mark; the item is still interrogative. Journal
        # hashes cover the prompt, not this validator, so tolerance changes
        # stay resume-safe.
        candidate = question.strip().rstrip("\"'”’").strip().replace("？", "?")
        if not candidate.endswith("?"):
            raise AuthoringError(f"item {index} has an invalid question or reference")
        key = _normalized(question)
        if key in seen:
            raise AuthoringError(f"item {index} repeats a question")
        seen.add(key)
        if not isinstance(evidence, list) or not evidence:
            raise AuthoringError(f"item {index} has no evidence")
        checked: list[dict[str, Any]] = []
        for cited in evidence:
            if not isinstance(cited, dict) or set(cited) != {"source_id", "chunk_id", "quote"}:
                raise AuthoringError(f"item {index} has malformed evidence")
            chunk_id = cited["chunk_id"]
            chunk = available.get(chunk_id)
            if chunk_id not in permitted or chunk is None or cited["source_id"] != chunk["source_id"]:
                raise AuthoringError(f"item {index} cites an unexpected source or chunk")
            quote = cited["quote"]
            if not isinstance(quote, str) or not quote.strip() or _normalized(quote) not in _normalized(chunk["text"]):
                raise AuthoringError(f"item {index} quote is not in the indexed chunk")
            locator = (f"slide {chunk['start_slide']}" if chunk.get("start_slide") is not None else
                       f"page {chunk['start_page']}" if chunk.get("start_page") is not None else None)
            checked.append({**cited, "locator": locator})
        digest = sha256((packet["course_id"] + "\n" + key).encode("utf-8")).hexdigest()[:16]
        questions.append(Question.model_validate({
            "sample_id": f"{packet['course_id']}-{digest}",
            "question": question, "reference_answer": answer, "evidence": checked,
            "answerable": True, "question_type": request.question_type,
            "difficulty": request.difficulty, "split": request.split,
            "authoring_method": "Kimi/OpenRouter source-bound draft",
            "integrity": "verified",
        }))
    return questions, {"requested": request.count, "valid": len(questions),
                       "deficit": request.count - len(questions)}


def assemble_draft_dataset(
    packet: dict[str, Any], batches: Sequence[Sequence[Question]], *,
    dataset_id: str, version: str,
) -> CourseDataset:
    """Assemble candidate rows; no field here represents human acceptance."""
    samples = [sample for batch in batches for sample in batch]
    if not samples:
        raise AuthoringError("no validated draft questions were produced")
    normalized = [_normalized(sample.question) for sample in samples]
    if len(set(normalized)) != len(normalized):
        raise AuthoringError("duplicate question across authoring batches")
    return CourseDataset.model_validate({
        "dataset_id": dataset_id, "version": version,
        "course_id": packet["course_id"], "notebook_id": packet["notebook_id"],
        "source_manifest": [
            {"source_id": source["source_id"], "source_label": source["source_label"],
             "notebook_id": packet["notebook_id"], "sha256": source["checksum_sha256"],
             "version_number": source["version_number"],
             "original_filename": source.get("original_filename")}
            for source in packet["sources"]
        ],
        "samples": [sample.model_dump(mode="json") for sample in samples],
    })
