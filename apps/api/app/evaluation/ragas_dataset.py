from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import re
from typing import Any, Iterable


class DatasetValidationError(ValueError):
    """Raised when an evaluation dataset cannot support a defensible run."""


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    source_label: str
    sha256: str
    notebook_id: str
    page_or_slide_range: str


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    source_id: str
    source_label: str
    text: str
    locator: str
    chunk_id: str


_SPACE_PATTERN = re.compile(r"\s+")
_SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_BULLET_PREFIX_PATTERN = re.compile(r"^[\s\-*.0-9:;()]+")
_COMMON_HEADING_WORDS = {"introduction", "overview", "summary", "references", "objectives", "contents"}


def build_draft_dataset(
    *,
    dataset_id: str,
    version: str,
    sources: Iterable[SourceRecord],
    chunks: Iterable[ChunkRecord],
    sample_count: int = 100,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Create source-traceable draft items from indexed chunks.

    This function is intentionally deterministic and labels every generated item
    as a draft. Approval is a separate human review decision.
    """

    source_records = list(sources)
    if not source_records:
        raise DatasetValidationError("At least one indexed source is required")
    if sample_count <= 0:
        raise DatasetValidationError("sample_count must be positive")

    source_ids = {source.source_id for source in source_records}
    candidates: list[dict[str, Any]] = []
    seen_questions: set[str] = set()

    for chunk in chunks:
        if chunk.source_id not in source_ids:
            continue
        for answer_index, answer in enumerate(_select_reference_answers(chunk.text), start=1):
            topic = _select_topic(chunk.text, answer, chunk.source_label)
            question = _make_question(answer, topic, chunk.source_label)
            normalized_question = _normalized(question)
            if normalized_question in seen_questions:
                question = f"According to {chunk.source_label}, what does the source state in {chunk.locator} about {topic}?"
                normalized_question = _normalized(question)
            if normalized_question in seen_questions:
                continue
            seen_questions.add(normalized_question)
            candidates.append(
                _draft_item(
                    chunk=chunk,
                    answer=answer,
                    question=question,
                    sample_number=len(candidates) + 1,
                    answer_index=answer_index,
                )
            )
            if len(candidates) == sample_count:
                break
        if len(candidates) == sample_count:
            break

    if len(candidates) != sample_count:
        raise DatasetValidationError(
            f"Only {len(candidates)} usable draft samples were generated; {sample_count} are required"
        )

    timestamp = (created_at or datetime.now(UTC)).isoformat()
    dataset = {
        "dataset_id": dataset_id,
        "version": version,
        "created_at_utc": timestamp,
        "source_manifest": [asdict(source) for source in source_records],
        "samples": candidates,
    }
    validate_dataset(dataset, require_approved=False, expected_sample_count=sample_count)
    return dataset


def validate_dataset(
    dataset: dict[str, Any],
    *,
    require_approved: bool,
    expected_sample_count: int = 100,
) -> None:
    """Validate the repository's RAGAS dataset contract without a runtime dependency."""

    if not isinstance(dataset, dict):
        raise DatasetValidationError("Dataset root must be an object")
    for required_key in ("dataset_id", "version", "source_manifest", "samples"):
        if not dataset.get(required_key):
            raise DatasetValidationError(f"Dataset is missing {required_key}")

    sources = dataset["source_manifest"]
    samples = dataset["samples"]
    if not isinstance(sources, list) or not isinstance(samples, list):
        raise DatasetValidationError("source_manifest and samples must be arrays")
    if len(samples) != expected_sample_count:
        raise DatasetValidationError(f"Dataset must contain exactly {expected_sample_count} samples")

    source_ids: set[str] = set()
    notebook_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise DatasetValidationError("Every source manifest entry must be an object")
        for key in ("source_id", "source_label", "sha256", "notebook_id"):
            if not str(source.get(key) or "").strip():
                raise DatasetValidationError(f"Source manifest entry is missing {key}")
        source_id = str(source["source_id"])
        if source_id in source_ids:
            raise DatasetValidationError(f"Source manifest contains duplicate source ID {source_id}")
        source_ids.add(source_id)
        notebook_ids.add(str(source["notebook_id"]))
    if len(notebook_ids) != 1:
        raise DatasetValidationError("All sources must belong to one frozen notebook")

    sample_ids: set[str] = set()
    questions: set[str] = set()
    allowed_statuses = {"draft", "approved", "rejected"}
    for sample in samples:
        if not isinstance(sample, dict):
            raise DatasetValidationError("Every sample must be an object")
        for key in (
            "sample_id",
            "question",
            "reference_answer",
            "reference_contexts",
            "source_ids",
            "source_chunk_id",
            "review_status",
        ):
            if key not in sample or not sample[key]:
                raise DatasetValidationError(f"Sample is missing {key}")
        sample_id = str(sample["sample_id"])
        if sample_id in sample_ids:
            raise DatasetValidationError(f"Dataset contains duplicate sample ID {sample_id}")
        sample_ids.add(sample_id)
        question = _normalized(str(sample["question"]))
        if len(question) < 8:
            raise DatasetValidationError(f"Sample {sample_id} question is too short")
        if question in questions:
            raise DatasetValidationError(f"Dataset contains duplicate question text for {sample_id}")
        questions.add(question)
        if not isinstance(sample["reference_contexts"], list) or not all(
            str(context).strip() for context in sample["reference_contexts"]
        ):
            raise DatasetValidationError(f"Sample {sample_id} must include non-empty reference contexts")
        if not isinstance(sample["source_ids"], list) or not set(sample["source_ids"]).issubset(source_ids):
            raise DatasetValidationError(f"Sample {sample_id} references a source outside the frozen manifest")
        status = str(sample["review_status"])
        if status not in allowed_statuses:
            raise DatasetValidationError(f"Sample {sample_id} has an invalid review status")
        if require_approved and status != "approved":
            raise DatasetValidationError(f"Sample {sample_id} is not approved for a final RAGAS run")


def _draft_item(
    *,
    chunk: ChunkRecord,
    answer: str,
    question: str,
    sample_number: int,
    answer_index: int,
) -> dict[str, Any]:
    return {
        "sample_id": f"eduground-ragas-{sample_number:03d}",
        "question": question,
        "reference_answer": answer,
        "reference_contexts": [chunk.text],
        "source_ids": [chunk.source_id],
        "expected_locator": chunk.locator,
        "question_type": "source-grounded factual",
        "difficulty": "mixed",
        "authoring_method": "deterministic indexed-chunk draft generator",
        "review_status": "draft",
        "source_chunk_id": chunk.chunk_id,
        "source_sentence_index": answer_index,
    }


def _select_reference_answers(text: str, *, limit: int = 6) -> list[str]:
    sentences = [_clean_sentence(part) for part in _SENTENCE_PATTERN.split(_normalized(text))]
    candidates = [sentence for sentence in sentences if _is_usable_sentence(sentence)]
    if not candidates:
        return []
    answers: list[str] = []
    for index, sentence in enumerate(candidates):
        answer = sentence
        if len(answer) < 140 and index + 1 < len(candidates):
            combined = f"{answer} {candidates[index + 1]}"
            if len(combined) <= 420:
                answer = combined
        if _normalized(answer) not in {_normalized(existing) for existing in answers}:
            answers.append(answer)
        if len(answers) == limit:
            break
    return answers


def _select_topic(text: str, answer: str, fallback: str) -> str:
    for raw_line in text.splitlines():
        line = _clean_sentence(raw_line)
        words = line.split()
        if 2 <= len(words) <= 10 and 6 <= len(line) <= 90:
            lowered = line.lower()
            if lowered not in _COMMON_HEADING_WORDS and lowered not in answer.lower():
                return line
    return fallback


def _make_question(answer: str, topic: str, source_label: str) -> str:
    patterns = (
        (r"^(.{4,90}?)\s+is\s+", "What is {term}?"),
        (r"^(.{4,90}?)\s+are\s+", "What are {term}?"),
        (r"^(.{4,90}?)\s+refers to\s+", "What does {term} refer to?"),
        (r"^(.{4,90}?)\s+includes\s+", "What does {term} include?"),
        (r"^(.{4,90}?)\s+involves\s+", "What does {term} involve?"),
        (r"^(.{4,90}?)\s+consists of\s+", "What does {term} consist of?"),
    )
    for pattern, template in patterns:
        match = re.match(pattern, answer, flags=re.IGNORECASE)
        if match:
            term = match.group(1).strip(" :;,.\"'")
            if 1 <= len(term.split()) <= 12 and not term.lower().startswith(("it ", "this ", "these ", "they ")):
                return template.format(term=term)
    if topic and topic.lower() not in _COMMON_HEADING_WORDS:
        return f"According to {source_label}, what key point is made about {topic}?"
    return f"What key concept is explained in {source_label}?"


def _is_usable_sentence(sentence: str) -> bool:
    lowered = sentence.lower()
    if len(sentence) < 40 or len(sentence) > 360:
        return False
    if len(re.findall(r"[a-z]{3,}", lowered)) < 8:
        return False
    disallowed = (
        "copyright",
        "learning objective",
        "learning outcome",
        "course code",
        "www.",
        "http://",
        "https://",
        "you are to submit",
        "fill in either",
    )
    return not any(token in lowered for token in disallowed)


def _clean_sentence(value: str) -> str:
    return _SPACE_PATTERN.sub(" ", _BULLET_PREFIX_PATTERN.sub("", value)).strip(" -:;,.\t")


def _normalized(value: str) -> str:
    return _SPACE_PATTERN.sub(" ", value).strip()
