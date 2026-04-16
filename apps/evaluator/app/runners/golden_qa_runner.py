from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.logging import get_logger
from app.models import EvaluationScore, GoldenQASample
from app.scorers.ragas_like import score_sample
from app.telemetry import bind_context, stage_span

logger = get_logger("evaluator.runners.golden_qa")


def load_samples(path: Path) -> list[GoldenQASample]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [GoldenQASample.model_validate(item) for item in raw]


def run_golden_qa(path: Path, *, run_id: str | None = None) -> list[EvaluationScore]:
    effective_run_id = run_id or uuid4().hex
    job_id = f"golden-qa:{path.stem}"
    with bind_context(job_id=job_id, run_id=effective_run_id):
        with stage_span("dataset.load", logger=logger, attributes={"dataset_path": str(path)}):
            samples = load_samples(path)
        scores: list[EvaluationScore] = []
        for sample in samples:
            with stage_span("sample.score", logger=logger, attributes={"sample_key": sample.sample_key}):
                scores.append(score_sample(sample))
        return scores
