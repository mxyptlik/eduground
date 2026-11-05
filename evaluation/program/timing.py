"""Monotonic, per-item stage timing for controlled answer collection."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter_ns
from typing import Callable, Iterator


@dataclass(frozen=True)
class StageDuration:
    name: str
    duration_ms: float
    status: str


class StageTimer:
    def __init__(self, clock_ns: Callable[[], int] = perf_counter_ns) -> None:
        self.clock_ns = clock_ns
        self.records: list[StageDuration] = []
        self._active: set[str] = set()

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        if not name or name in self._active:
            raise ValueError("stage name must be nonempty and not already active")
        self._active.add(name)
        started = self.clock_ns()
        status = "completed"
        try:
            yield
        except BaseException:
            status = "failed"
            raise
        finally:
            finished = self.clock_ns()
            self._active.remove(name)
            if finished < started:
                raise ValueError("monotonic clock moved backwards")
            self.records.append(StageDuration(name, (finished - started) / 1_000_000, status))

    def summary(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for record in self.records:
            totals[record.name] = totals.get(record.name, 0.0) + record.duration_ms
        return totals
