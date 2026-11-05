import pytest

from evaluation.program.timing import StageTimer


def test_retrieval_and_generation_durations_remain_separate():
    ticks = iter([0, 3_000_000, 5_000_000, 13_000_000])
    timer = StageTimer(clock_ns=lambda: next(ticks))
    with timer.measure("retrieval"):
        pass
    with timer.measure("generation"):
        pass
    assert timer.summary() == {"retrieval": 3.0, "generation": 8.0}


def test_failed_stage_keeps_duration_and_outcome():
    ticks = iter([1, 4])
    timer = StageTimer(clock_ns=lambda: next(ticks))
    with pytest.raises(RuntimeError):
        with timer.measure("rerank"):
            raise RuntimeError("provider failed")
    assert timer.records[0].status == "failed"
    assert timer.records[0].duration_ms == 0.000003
