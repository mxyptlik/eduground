from __future__ import annotations

from app.models import EvaluationScore, GoldenQASample


def test_golden_qa_sample_validation_accepts_complete_payload() -> None:
    sample = GoldenQASample.model_validate(
        {
            "sample_key": "sample-1",
            "question": "Q",
            "expected_answer": "A",
            "retrieved_context": "C",
            "model_answer": "R",
        }
    )

    assert sample.sample_key == "sample-1"


def test_evaluation_score_serializes_pass_fail() -> None:
    score = EvaluationScore(
        sample_key="sample-1",
        faithfulness_score=0.8,
        context_recall_score=0.7,
        pass_fail=True,
    )

    assert score.model_dump()["pass_fail"] is True
