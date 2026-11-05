import pytest

from evaluation.program.authoring import AuthoringError
from evaluation.program.blueprints import blueprint_plan, parse_blueprint


def packet():
    return {"course_id": "physics", "sources": [{"source_id": "s1", "source_label": "Lecture",
            "chunks": [{"chunk_id": "c1", "text": "Force equals mass times acceleration."},
                       {"chunk_id": "c2", "text": "Acceleration measures change."}]}]}


def blueprint():
    return {"course_id": "physics", "batches": [
        {"count": 2, "question_type": "factual", "difficulty": "easy", "split": "development", "chunk_ids": ["c1"]},
        {"count": 1, "question_type": "synthesis", "difficulty": "hard", "split": "held_out", "chunk_ids": ["c2"]}]}


def test_plan_counts_and_prompt_hashes_without_provider_calls():
    parsed = parse_blueprint(blueprint(), packet())
    plan = blueprint_plan(parsed, packet())
    assert plan["total_requested"] == 3
    assert plan["targets_by_split"] == {"development": 2, "held_out": 1}
    assert plan["targets_by_type"] == {"factual": 2, "synthesis": 1}
    assert all(len(row["prompt_sha256"]) == 64 for row in plan["batches"])
    assert plan["provider_calls_made"] == 0


@pytest.mark.parametrize("change", [
    lambda b: b.update(course_id="other"),
    lambda b: b["batches"][0].update(chunk_ids=["absent"]),
    lambda b: b["batches"][0].update(count=11),
    lambda b: b["batches"][0].update(split="unknown"),
    lambda b: b["batches"][1].update(chunk_ids=["c1"]),
])
def test_invalid_course_chunks_or_targets_rejected(change):
    value = blueprint()
    change(value)
    with pytest.raises(AuthoringError):
        parse_blueprint(value, packet())
