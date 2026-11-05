"""Reproducible paired score changes between matched answer conditions."""

from __future__ import annotations

from random import Random
from statistics import mean
from typing import Iterable

from evaluation.program.reports.distributions import ScoreObservation, _quantile


def paired_bootstrap(
    observations: Iterable[ScoreObservation], *,
    control: str, treatment: str, metric_id: str,
    draws: int = 2000, seed: int = 0,
) -> dict:
    """Resample question identities, not individual condition scores."""
    if control == treatment:
        raise ValueError("control and treatment must differ")
    if isinstance(draws, bool) or draws < 100:
        raise ValueError("at least 100 bootstrap draws are required")
    matched: dict[tuple[str, str], dict[str, ScoreObservation]] = {}
    for row in observations:
        if row.metric_id != metric_id or row.condition not in {control, treatment}:
            continue
        key = (row.course_id, row.sample_id)
        if row.condition in matched.setdefault(key, {}):
            raise ValueError("duplicate condition score for a question")
        matched[key][row.condition] = row
    differences: list[float] = []
    excluded = {"missing_pair": 0, "not_applicable": 0, "failed": 0}
    for pair in matched.values():
        if control not in pair or treatment not in pair:
            excluded["missing_pair"] += 1
        elif any(row.status == "failed" for row in pair.values()):
            excluded["failed"] += 1
        elif any(row.status == "not_applicable" for row in pair.values()):
            excluded["not_applicable"] += 1
        else:
            differences.append(float(pair[treatment].value) - float(pair[control].value))
    if not differences:
        return {"metric_id": metric_id, "control": control, "treatment": treatment,
                "paired_count": 0, "excluded": excluded, "mean_change": None,
                "ci95_low": None, "ci95_high": None, "draws": draws, "seed": seed}
    random = Random(seed)
    n = len(differences)
    estimates = sorted(mean(differences[random.randrange(n)] for _ in range(n)) for _ in range(draws))
    return {"metric_id": metric_id, "control": control, "treatment": treatment,
            "paired_count": n, "excluded": excluded, "mean_change": mean(differences),
            "ci95_low": _quantile(estimates, 0.025), "ci95_high": _quantile(estimates, 0.975),
            "draws": draws, "seed": seed}
