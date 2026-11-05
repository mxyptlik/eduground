from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation.program.contracts import CourseDataset, ContractError, adapt_legacy_v1


ROOT = Path(__file__).resolve().parents[3]


def test_existing_hundred_question_file_loads_as_unchecked_drafts():
    source = ROOT / "evaluation/ragas/datasets/eduground_100q.gemini-v1.approved.json"
    dataset = adapt_legacy_v1(json.loads(source.read_text(encoding="utf-8")), course_id="CSC442")
    assert len(dataset.samples) == 100
    assert {sample.integrity for sample in dataset.samples} == {"unchecked"}
    assert len({sample.sample_id for sample in dataset.samples}) == 100
    assert all(sample.evidence for sample in dataset.samples)
    assert dataset.notebook_id == dataset.source_manifest[0].notebook_id


def test_variable_count_and_multiple_evidence_units():
    data = {
        "dataset_id": "pilot", "version": "v2", "course_id": "CSC442", "notebook_id": "notebook",
        "source_manifest": [{"source_id": "s1", "source_label": "A", "notebook_id": "notebook", "sha256": "a" * 64}],
        "samples": [{"sample_id": "q1", "question": "What are both reasons?", "reference_answer": "A and B.",
                     "evidence": [{"source_id": "s1", "chunk_id": "c1", "quote": "A"},
                                  {"source_id": "s1", "chunk_id": "c2", "quote": "B"}]}],
    }
    result = CourseDataset.model_validate(data)
    assert len(result.samples) == 1 and len(result.samples[0].evidence) == 2
    with pytest.raises(ValidationError, match="unknown source"):
        CourseDataset.model_validate({**data, "samples": [{**data["samples"][0], "evidence": [{"source_id": "wrong", "chunk_id": "c1", "quote": "A"}]}]})


def test_unanswerable_question_requires_no_evidence_and_no_reference():
    source = {"source_id": "s1", "source_label": "A", "notebook_id": "notebook", "sha256": "a" * 64}
    base = {"dataset_id": "pilot", "version": "v2", "course_id": "CSC442", "notebook_id": "notebook", "source_manifest": [source]}
    item = {"sample_id": "q1", "question": "What fact is absent?", "answerable": False}
    assert not CourseDataset.model_validate({**base, "samples": [item]}).samples[0].answerable
    with pytest.raises(ValidationError, match="cannot have supporting evidence"):
        CourseDataset.model_validate({**base, "samples": [{**item, "evidence": [{"source_id": "s1", "chunk_id": "c1", "quote": "A"}]}]})


def test_legacy_missing_chunk_cannot_be_invented():
    with pytest.raises(ContractError, match="lacks source evidence"):
        adapt_legacy_v1({"source_manifest": [{"notebook_id": "n"}], "samples": [{"sample_id": "q"}]}, course_id="CSC442")
