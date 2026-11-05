from evaluation.program.contracts import CourseDataset
from evaluation.program.draft_leakage import draft_leakage_report


def _dataset(question2="How does acceleration change velocity?", chunk2="c2"):
    return CourseDataset.model_validate({
        "course_id": "physics", "notebook_id": "n1", "dataset_id": "draft", "version": "1",
        "source_manifest": [{"source_id": "s1", "source_label": "Lecture", "notebook_id": "n1",
                             "sha256": "a" * 64}],
        "samples": [
            {"sample_id": "q1", "question": "How does acceleration change velocity?",
             "reference_answer": "It changes velocity over time.", "split": "development",
             "evidence": [{"source_id": "s1", "chunk_id": "c1", "quote": "Acceleration changes velocity."}]},
            {"sample_id": "q2", "question": question2,
             "reference_answer": "It changes velocity over time.", "split": "held_out",
             "evidence": [{"source_id": "s1", "chunk_id": chunk2, "quote": "Acceleration changes velocity."}]},
        ],
    })


def _packet():
    return {"course_id": "physics", "sources": [{"chunks": [
        {"chunk_id": "c1", "text": "Acceleration changes velocity over time."},
        {"chunk_id": "c2", "text": "Acceleration changes velocity over time."}]}]}


def test_exact_question_and_shared_evidence_block_freeze():
    report = draft_leakage_report(_dataset(chunk2="c1"), _packet())
    assert {flag["kind"] for flag in report["flags"]} == {"exact_question", "shared_chunk_across_splits"}
    assert report["blocking_count"] == 2


def test_overlapping_chunk_text_requires_human_disposition():
    report = draft_leakage_report(_dataset(question2="Why does an object's motion vary?"), _packet())
    assert [flag["kind"] for flag in report["flags"]] == ["overlapping_chunk_text_across_splits"]
    assert report["blocking_count"] == 0
    assert report["human_disposition_required"] is True
