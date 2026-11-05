"""Validated course, evidence and question contracts for evaluation programme v2.

Legacy v1 files are adapted as drafts. Their automatic ``approved`` labels are
never interpreted as an independent human decision.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractError(ValueError):
    """A dataset cannot be used under the evaluation programme contract."""


class Split(StrEnum):
    DEVELOPMENT = "development"
    HELD_OUT = "held_out"


class IntegrityState(StrEnum):
    UNCHECKED = "unchecked"
    VERIFIED = "verified"
    FAILED = "failed"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SourceManifest(FrozenModel):
    source_id: str = Field(min_length=1)
    source_label: str = Field(min_length=1)
    notebook_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    version_number: int | None = Field(default=None, ge=1)
    original_filename: str | None = None
    page_or_slide_range: str | None = None
    extraction_method: str | None = None


class Evidence(FrozenModel):
    source_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    locator: str | None = None
    evidence_group: str | None = None


class Question(FrozenModel):
    sample_id: str = Field(min_length=1)
    question: str = Field(min_length=8)
    reference_answer: str | None = None
    evidence: tuple[Evidence, ...] = ()
    answerable: bool = True
    question_type: str | None = None
    difficulty: str | None = None
    split: Split = Split.DEVELOPMENT
    authoring_method: str | None = None
    integrity: IntegrityState = IntegrityState.UNCHECKED

    @model_validator(mode="after")
    def check_evidence(self) -> Question:
        if self.answerable and (not self.reference_answer or not self.evidence):
            raise ValueError("Answerable questions require a reference answer and evidence")
        if not self.answerable and self.evidence:
            raise ValueError("Unanswerable questions cannot have supporting evidence")
        return self


class CourseDataset(FrozenModel):
    schema_version: int = Field(default=2, ge=2, le=2)
    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    course_id: str = Field(min_length=1)
    notebook_id: str = Field(min_length=1)
    created_at_utc: datetime | None = None
    source_manifest: tuple[SourceManifest, ...] = Field(min_length=1)
    samples: tuple[Question, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_links(self) -> CourseDataset:
        source_ids = [source.source_id for source in self.source_manifest]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Source IDs must be unique")
        if any(source.notebook_id != self.notebook_id for source in self.source_manifest):
            raise ValueError("All sources must belong to the dataset notebook")
        sample_ids = [sample.sample_id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("Sample IDs must be unique within a course dataset")
        for sample in self.samples:
            if any(evidence.source_id not in source_ids for evidence in sample.evidence):
                raise ValueError(f"Question {sample.sample_id} references an unknown source")
        return self


def adapt_legacy_v1(payload: dict[str, Any], *, course_id: str) -> CourseDataset:
    """Read a v1 source-grounded dataset as *draft* v2 data.

    Importing a historical file is not evidence of human acceptance, even when
    every v1 row says ``approved``. A separate review release is required.
    """
    if payload.get("schema_version") == 2:
        return CourseDataset.model_validate(payload)
    sources = payload.get("source_manifest")
    samples = payload.get("samples")
    if not isinstance(sources, list) or not sources or not isinstance(samples, list) or not samples:
        raise ContractError("Legacy dataset needs non-empty source_manifest and samples")
    notebooks = {str(source.get("notebook_id", "")) for source in sources if isinstance(source, dict)}
    if len(notebooks) != 1 or not next(iter(notebooks)):
        raise ContractError("Legacy source manifest must identify exactly one notebook")
    adapted: list[dict[str, Any]] = []
    for row in samples:
        if not isinstance(row, dict):
            raise ContractError("Legacy sample must be an object")
        source_ids = row.get("source_ids") or []
        contexts = row.get("reference_contexts") or []
        chunk_id = row.get("source_chunk_id")
        if not isinstance(source_ids, list) or not isinstance(contexts, list) or not source_ids or not contexts or not chunk_id:
            raise ContractError(f"Legacy sample {row.get('sample_id')} lacks source evidence")
        # Legacy records have a single chunk ID. Do not pretend a row with
        # several source IDs identifies which other chunks support each quote.
        evidence = [
            {"source_id": source_ids[0], "chunk_id": chunk_id, "quote": text, "locator": row.get("expected_locator")}
            for text in contexts
        ]
        adapted.append({
            "sample_id": row.get("sample_id"),
            "question": row.get("question"),
            "reference_answer": row.get("reference_answer"),
            "evidence": evidence,
            "question_type": row.get("question_type"),
            "difficulty": row.get("difficulty"),
            "authoring_method": row.get("authoring_method"),
            "integrity": "unchecked",
            "split": "development",
        })
    try:
        return CourseDataset.model_validate({
            "dataset_id": payload.get("dataset_id"),
            "version": payload.get("version"),
            "course_id": course_id,
            "notebook_id": next(iter(notebooks)),
            "created_at_utc": payload.get("created_at_utc"),
            "source_manifest": [
                {key: source[key] for key in SourceManifest.model_fields if key in source}
                for source in sources
            ],
            "samples": adapted,
        })
    except Exception as exc:
        raise ContractError(f"Legacy dataset cannot be adapted: {exc}") from exc
