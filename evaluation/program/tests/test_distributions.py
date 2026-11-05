import pytest

from evaluation.program.reports.distributions import ScoreObservation, summarize_group, summarize_scores


def _row(sample, status, value, course="c1"):
    return ScoreObservation(course, sample, "no_retrieval", "factual_correctness_f1", status, value)


def test_variation_and_denominators_are_visible():
    rows = [_row("q1", "completed", 0.0), _row("q2", "completed", 1.0),
            _row("q3", "not_applicable", None), _row("q4", "failed", None)]
    summary = summarize_group(rows)
    assert summary["mean"] == summary["median"] == 0.5
    assert summary["min"] == 0 and summary["max"] == 1
    assert summary["population_stddev"] == 0.5
    assert summary["histogram_0_to_1_by_fifths"] == [1, 0, 0, 0, 1]
    assert (summary["completed_count"], summary["not_applicable_count"], summary["failed_count"]) == (2, 1, 1)


def test_no_completed_scores_have_null_statistics():
    assert summarize_group([_row("q1", "failed", None)])["mean"] is None


def test_duplicate_and_nonfinite_scores_rejected():
    with pytest.raises(ValueError, match="finite"):
        _row("q1", "completed", float("nan"))
    with pytest.raises(ValueError, match="duplicate"):
        summarize_scores([_row("q1", "completed", 0.3), _row("q1", "completed", 0.4)])
