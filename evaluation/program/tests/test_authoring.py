import json

import pytest

from evaluation.program.authoring import (
    AuthoringError, QuestionRequest, assemble_draft_dataset, author_prompt, parse_kimi_drafts,
)


def _packet():
    return {"course_id": "physics", "notebook_id": "n1", "sources": [
        {"source_id": "s1", "source_label": "Lecture", "checksum_sha256": "a" * 64,
         "version_number": 1, "original_filename": "lecture.pdf", "chunks": [
             {"chunk_id": "c1", "text": "Force equals mass times acceleration.", "start_page": 2, "start_slide": None},
             {"chunk_id": "c2", "text": "Acceleration is the rate of change of velocity.", "start_page": 3, "start_slide": None},
         ]}
    ]}


def _response(quote="Force equals mass times acceleration."):
    return json.dumps({"items": [{"question": "How are force, mass and acceleration related?",
        "reference_answer": "Force equals mass times acceleration.",
        "evidence": [{"source_id": "s1", "chunk_id": "c1", "quote": quote}]}]})


def test_source_bound_prompt_and_variable_count_deficit():
    request = QuestionRequest(3, "relationship", "medium", "development", ("c1", "c2"))
    prompt, digest = author_prompt(_packet(), request)
    assert "Kimi" in prompt and "c1" in prompt and "c2" in prompt and len(digest) == 64
    questions, report = parse_kimi_drafts(_packet(), request, _response())
    assert len(questions) == 1 and report == {"requested": 3, "valid": 1, "deficit": 2}
    assert questions[0].integrity == "verified"
    assert questions[0].evidence[0].locator == "page 2"
    dataset = assemble_draft_dataset(_packet(), [questions], dataset_id="draft-p", version="1")
    assert len(dataset.samples) == 1 and dataset.course_id == "physics"


def test_hallucinated_quote_and_chunk_are_rejected():
    request = QuestionRequest(1, "relationship", "easy", "held_out", ("c1",))
    with pytest.raises(AuthoringError, match="not in the indexed chunk"):
        parse_kimi_drafts(_packet(), request, _response("Invented quote"))
    payload = json.loads(_response())
    payload["items"][0]["evidence"][0]["chunk_id"] = "c2"
    with pytest.raises(AuthoringError, match="unexpected"):
        parse_kimi_drafts(_packet(), request, json.dumps(payload))


def test_multiple_evidence_units_are_retained():
    request = QuestionRequest(1, "synthesis", "hard", "development", ("c1", "c2"))
    payload = json.loads(_response())
    payload["items"][0]["evidence"].append({"source_id": "s1", "chunk_id": "c2",
        "quote": "Acceleration is the rate of change of velocity."})
    questions, _ = parse_kimi_drafts(_packet(), request, json.dumps(payload))
    assert [e.chunk_id for e in questions[0].evidence] == ["c1", "c2"]
