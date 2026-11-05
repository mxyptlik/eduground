"""Tests for the M1.7 cross-course leakage disposition report."""

from __future__ import annotations

from evaluation.program.contracts import CourseDataset
from evaluation.program.leakage import cross_course_leakage_report

HASH_A = "a" * 64


def _dataset(course_id: str, sample_id: str, question: str, chunk_id: str) -> CourseDataset:
    return CourseDataset(
        dataset_id=f"{course_id}-v1", version="1", course_id=course_id, notebook_id=f"{course_id}-n",
        source_manifest=[{"source_id": f"{course_id}-s1", "source_label": "Lecture",
                          "notebook_id": f"{course_id}-n", "sha256": HASH_A}],
        samples=[{"sample_id": sample_id, "question": question, "reference_answer": "A",
                  "evidence": [{"source_id": f"{course_id}-s1", "chunk_id": chunk_id, "quote": "q"}]}],
    )


def _inventory():
    return {
        "documents": [
            {"course_id": "CSC1", "relative_path": "CSC1/lecture-1.pdf", "suffix": ".pdf",
             "byte_size": 1000, "sha256": "1" * 64},
            {"course_id": "CSC2", "relative_path": "CSC2/lecture-1.pdf", "suffix": ".pdf",
             "byte_size": 1000, "sha256": "1" * 64},
            {"course_id": "CSC2", "relative_path": "CSC2/slides.pdf", "suffix": ".pdf",
             "byte_size": 2000, "sha256": "2" * 64},
            {"course_id": "CSC3", "relative_path": "CSC3/slides.pdf", "suffix": ".pdf",
             "byte_size": 2020, "sha256": "3" * 64},
        ],
        "duplicates": [{"sha256": "1" * 64,
                        "paths": ["CSC1/lecture-1.pdf", "CSC2/lecture-1.pdf"],
                        "course_ids": ["CSC1", "CSC2"]}],
    }


def test_cross_course_duplicates_near_duplicates_and_questions_are_flagged():
    datasets = [
        _dataset("CSC1", "q1", "What is a stack?", "chunk-shared"),
        _dataset("CSC2", "q1", "What is a stack?", "chunk-shared"),
        _dataset("CSC3", "q1", "Explain virtual memory paging.", "chunk-unique"),
    ]
    report = cross_course_leakage_report(_inventory(), datasets)
    assert report["summary"]["cross_course_duplicate_groups"] == 1
    assert report["summary"]["near_duplicate_candidates"] == 1
    near = report["near_duplicate_candidates"][0]
    assert near["courses"] == ["CSC2", "CSC3"]
    assert near["status"] == "requires_human_disposition"
    pairs = report["cross_course_question_overlap"]
    assert len(pairs) == 1 and {pairs[0]["left"]["course_id"], pairs[0]["right"]["course_id"]} == {"CSC1", "CSC2"}
    shared = report["shared_evidence_chunks"]
    assert len(shared) == 1 and shared[0]["chunk_id"] == "chunk-shared"
    assert report["summary"]["disposition_required"] is True
    assert report["report_only"] is True
    assert "removed" in report["note"]


def test_unique_courses_produce_no_disposition():
    datasets = [
        _dataset("CSC1", "q1", "What is a stack?", "chunk-a"),
        _dataset("CSC2", "q1", "Describe process scheduling.", "chunk-b"),
    ]
    report = cross_course_leakage_report({"documents": [], "duplicates": []}, datasets)
    assert report["summary"]["disposition_required"] is False
    assert report["cross_course_question_overlap"] == []
