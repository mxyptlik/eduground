from __future__ import annotations

from app.runners.golden_qa_runner import load_samples, run_golden_qa


def test_load_samples_returns_typed_models(sample_dataset_file) -> None:
    samples = load_samples(sample_dataset_file)

    assert len(samples) == 2
    assert samples[0].sample_key == "sample-1"


def test_run_golden_qa_returns_score_per_sample(sample_dataset_file) -> None:
    scores = run_golden_qa(sample_dataset_file)

    assert [score.sample_key for score in scores] == ["sample-1", "sample-2"]
    assert all(isinstance(score.pass_fail, bool) for score in scores)
