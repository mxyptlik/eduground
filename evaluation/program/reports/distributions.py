"""Score distributions with denominators, N/A and failure counts."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import mean, median, pstdev
from typing import Iterable


@dataclass(frozen=True)
class ScoreObservation:
    course_id: str
    sample_id: str
    condition: str
    metric_id: str
    status: str
    value: float | None

    def __post_init__(self) -> None:
        if not all((self.course_id, self.sample_id, self.condition, self.metric_id)):
            raise ValueError("score identity fields are required")
        if self.status not in {"completed", "not_applicable", "failed"}:
            raise ValueError("invalid score status")
        if self.status == "completed":
            if (self.value is None or isinstance(self.value, bool) or
                    not isinstance(self.value, (int, float)) or not isfinite(self.value) or not 0 <= self.value <= 1):
                raise ValueError("completed score must be finite within 0..1")
        elif self.value is not None:
            raise ValueError("N/A or failed score cannot have a numeric value")


def _quantile(sorted_values: list[float], p: float) -> float:
    position = (len(sorted_values) - 1) * p
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def summarize_group(rows: Iterable[ScoreObservation]) -> dict:
    rows = tuple(rows)
    completed = sorted(float(row.value) for row in rows if row.status == "completed")
    return {
        "sample_count": len(rows),
        "completed_count": len(completed),
        "not_applicable_count": sum(row.status == "not_applicable" for row in rows),
        "failed_count": sum(row.status == "failed" for row in rows),
        "mean": mean(completed) if completed else None,
        "median": median(completed) if completed else None,
        "min": completed[0] if completed else None,
        "max": completed[-1] if completed else None,
        "p10": _quantile(completed, 0.10) if completed else None,
        "p25": _quantile(completed, 0.25) if completed else None,
        "p75": _quantile(completed, 0.75) if completed else None,
        "p90": _quantile(completed, 0.90) if completed else None,
        "population_stddev": pstdev(completed) if completed else None,
        "histogram_0_to_1_by_fifths": [
            sum((index / 5 <= value < (index + 1) / 5) if index < 4 else (0.8 <= value <= 1)
                for value in completed)
            for index in range(5)
        ],
    }


def summarize_scores(observations: Iterable[ScoreObservation]) -> dict:
    rows = tuple(observations)
    keys = [(row.course_id, row.sample_id, row.condition, row.metric_id) for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate score observation")
    by_course = {
        f"{course_id}|{condition}|{metric_id}": summarize_group(
            row for row in rows if (row.course_id, row.condition, row.metric_id) == (course_id, condition, metric_id)
        )
        for course_id, condition, metric_id in sorted({(r.course_id, r.condition, r.metric_id) for r in rows})
    }
    by_condition = {
        f"{condition}|{metric_id}": summarize_group(
            row for row in rows if (row.condition, row.metric_id) == (condition, metric_id)
        )
        for condition, metric_id in sorted({(r.condition, r.metric_id) for r in rows})
    }
    return {"schema_version": 1, "by_course": by_course, "by_condition_micro": by_condition}
