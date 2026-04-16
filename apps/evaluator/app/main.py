from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.logging import configure_logging, get_logger
from app.runners.golden_qa_runner import run_golden_qa
from app.telemetry import bind_context, configure_telemetry, stage_span

configure_logging("evaluator")
configure_telemetry("curriculum-tutor-evaluator")
logger = get_logger("evaluator.main")


def main() -> None:
    dataset_path = Path(__file__).resolve().parent / "datasets" / "golden_qa.sample.json"
    run_id = uuid4().hex
    job_id = f"evaluation:{dataset_path.stem}"
    with bind_context(job_id=job_id, run_id=run_id):
        with stage_span("evaluation.main", logger=logger, attributes={"dataset_path": str(dataset_path)}):
            scores = run_golden_qa(dataset_path, run_id=run_id)
            logger.info(
                "Completed golden QA evaluation run",
                extra={"extra_json": {"dataset_path": str(dataset_path), "sample_count": len(scores)}},
            )
            for score in scores:
                print(score.model_dump_json())


if __name__ == "__main__":
    main()
