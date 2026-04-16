from __future__ import annotations

from pathlib import Path

from app import telemetry
from app.runners.golden_qa_runner import run_golden_qa
from app.scorers.ragas_like import score_sample


def test_sample_dataset_runs_end_to_end() -> None:
    dataset_path = Path(__file__).resolve().parents[2] / "app" / "datasets" / "golden_qa.sample.json"
    telemetry.reset_test_state()

    scores = run_golden_qa(dataset_path)

    assert len(scores) == 1
    assert scores[0].sample_key == "intro-1"
    assert scores[0].faithfulness_score >= 0
    stage_names = {event.attributes["stage"] for event in telemetry.get_metric_events() if "stage" in event.attributes}
    assert {"dataset.load", "sample.score"} <= stage_names


def test_scoring_is_deterministic_for_same_input(sample_dataset_file) -> None:
    scores_one = run_golden_qa(sample_dataset_file)
    scores_two = run_golden_qa(sample_dataset_file)

    assert [score.model_dump() for score in scores_one] == [score.model_dump() for score in scores_two]


def test_scoring_flags_low_grounding_as_failure() -> None:
    from app.models import GoldenQASample

    sample = GoldenQASample(
        sample_key="bad-1",
        question="What is grounding?",
        expected_answer="Grounding ties answers to notebook evidence.",
        retrieved_context="This context is unrelated.",
        model_answer="This answer invents details.",
    )

    score = score_sample(sample)

    assert score.pass_fail is False
