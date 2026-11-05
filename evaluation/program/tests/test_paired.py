import pytest

from evaluation.program.reports.distributions import ScoreObservation
from evaluation.program.reports.paired import paired_bootstrap


def _row(sample, condition, value, status="completed"):
    return ScoreObservation("c1", sample, condition, "faithfulness", status, value)


def test_paired_change_and_interval_are_reproducible():
    rows = [_row("q1", "no_rerank", 0.2), _row("q1", "rerank", 0.5),
            _row("q2", "no_rerank", 0.4), _row("q2", "rerank", 0.6)]
    report = paired_bootstrap(rows, control="no_rerank", treatment="rerank",
                              metric_id="faithfulness", draws=200, seed=11)
    assert report == paired_bootstrap(rows, control="no_rerank", treatment="rerank",
                                      metric_id="faithfulness", draws=200, seed=11)
    assert report["paired_count"] == 2
    assert report["mean_change"] == pytest.approx(0.25)
    assert 0.19 <= report["ci95_low"] <= report["ci95_high"] <= 0.31


def test_failed_and_missing_pairs_are_not_silently_dropped():
    rows = [_row("q1", "no_rerank", 0.2), _row("q1", "rerank", None, "failed"),
            _row("q2", "no_rerank", 0.4)]
    report = paired_bootstrap(rows, control="no_rerank", treatment="rerank", metric_id="faithfulness")
    assert report["paired_count"] == 0
    assert report["excluded"] == {"missing_pair": 1, "not_applicable": 0, "failed": 1}
    assert report["mean_change"] is None
