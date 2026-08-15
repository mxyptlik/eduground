from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from evaluation.ragas.ragas_compat import install_vertexai_import_compatibility  # noqa: E402


def _load_inputs(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        for field in ("sample_id", "user_input", "response", "retrieved_contexts", "reference"):
            if not row.get(field):
                raise ValueError(f"Input line {line_number} is missing {field}")
        if not isinstance(row["retrieved_contexts"], list):
            raise ValueError(f"Input line {line_number} retrieved_contexts must be an array")
        rows.append(row)
    if not rows:
        raise ValueError("No RAGAS inputs were found")
    return rows


def _score_one(*, row: dict[str, Any], llm: Any) -> dict[str, Any]:
    install_vertexai_import_compatibility()
    from ragas.metrics.collections import ContextRecall, Faithfulness

    async def score() -> tuple[Any, Any]:
        faithfulness = Faithfulness(llm=llm)
        context_recall = ContextRecall(llm=llm)
        return await asyncio.gather(
            faithfulness.ascore(
                user_input=row["user_input"],
                response=row["response"],
                retrieved_contexts=row["retrieved_contexts"],
            ),
            context_recall.ascore(
                user_input=row["user_input"],
                retrieved_contexts=row["retrieved_contexts"],
                reference=row["reference"],
            ),
        )

    faithfulness_result, context_recall_result = asyncio.run(score())
    return {
        "faithfulness": _score_value({"faithfulness": faithfulness_result.value}, "faithfulness"),
        "context_recall": _score_value({"context_recall": context_recall_result.value}, "context_recall"),
    }


def _score_value(record: dict[str, Any], field: str) -> float | None:
    value = record.get(field)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"RAGAS returned a non-numeric {field} score") from exc


def _build_llm() -> Any:
    if not settings.gemini_api_key:
        raise RuntimeError("CURRICULUM_TUTOR_GEMINI_API_KEY is required for real RAGAS evaluation")
    install_vertexai_import_compatibility()
    import instructor
    from google import genai
    from ragas.llms.adapters.instructor import InstructorLLM

    client = genai.Client(api_key=settings.gemini_api_key)
    # RAGAS 0.4.3's llm_factory creates a synchronous Google wrapper, while the
    # current Faithfulness and ContextRecall metrics invoke agenerate().
    async_client = instructor.from_genai(client, use_async=True)
    return InstructorLLM(
        client=async_client,
        model=settings.gemini_chat_model,
        provider="google",
    )


def _summary(scores: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [score for score in scores if score.get("status") == "completed"]

    def mean_for(metric: str) -> float | None:
        values = [float(score[metric]) for score in completed if score.get(metric) is not None]
        return round(sum(values) / len(values), 4) if values else None

    return {
        "sample_count": len(scores),
        "completed_samples": len(completed),
        "failed_samples": len(scores) - len(completed),
        "average_faithfulness": mean_for("faithfulness"),
        "average_context_recall": mean_for("context_recall"),
        "metrics": ["faithfulness", "context_recall"],
    }


def _summary_markdown(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Real RAGAS Evaluation Summary",
            "",
            f"- Framework: {summary['framework']} {summary['ragas_version']}",
            f"- Evaluator: {summary['evaluator_provider']} / {summary['evaluator_model']}",
            f"- Samples: {summary['sample_count']}",
            f"- Completed: {summary['completed_samples']}",
            f"- Failed: {summary['failed_samples']}",
            f"- Mean faithfulness: {summary['average_faithfulness']}",
            f"- Mean context recall: {summary['average_context_recall']}",
            "",
            "A run with failed samples is incomplete and must not be reported as a final result.",
            "",
        ]
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _package_versions() -> dict[str, str]:
    packages = ("ragas", "google-genai", "instructor", "jsonref")
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def _write_scoring_config(*, args: argparse.Namespace, rows: list[dict[str, Any]]) -> None:
    config = {
        "framework": "RAGAS",
        "ragas_version": _ragas_version(),
        "metrics": ["faithfulness", "context_recall"],
        "metric_execution": "ragas.metrics.collections direct asynchronous ascore()",
        "evaluator_provider": "Google Gemini",
        "evaluator_model": settings.gemini_chat_model,
        "gemini_adapter": "instructor.from_genai(..., use_async=True)",
        "sample_count": len(rows),
        "input_file": str(args.inputs),
        "input_sha256": _file_sha256(args.inputs),
        "max_retries": args.max_retries,
        "retry_delay_seconds": args.retry_delay_seconds,
        "sample_delay_seconds": args.delay_seconds,
        "packages": _package_versions(),
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    (args.run_dir / "scoring-config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Score collected EduGround responses with real RAGAS and Gemini.")
    parser.add_argument("--inputs", type=Path, required=True, help="ragas-inputs.jsonl from collect_ragas_inputs.py")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--retry-delay-seconds", type=float, default=15.0)
    parser.add_argument("--delay-seconds", type=float, default=12.0, help="Delay between samples to respect evaluator-model request limits.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    scores_path = args.run_dir / "ragas-scores.jsonl"
    summary_path = args.run_dir / "summary.json"
    if (scores_path.exists() or summary_path.exists()) and not args.force:
        raise SystemExit(f"Refusing to overwrite existing RAGAS outputs in {args.run_dir}")
    if args.max_retries < 0:
        raise SystemExit("--max-retries must be zero or greater")
    if args.delay_seconds < 0:
        raise SystemExit("--delay-seconds must be zero or greater")

    rows = _load_inputs(args.inputs)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    _write_scoring_config(args=args, rows=rows)
    llm = _build_llm()
    scores: list[dict[str, Any]] = []
    with scores_path.open("w", encoding="utf-8") as output:
        for index, row in enumerate(rows, start=1):
            result: dict[str, Any] = {
                "sample_id": row["sample_id"],
                "attempts": 0,
                "status": "failed",
                "evaluated_at_utc": datetime.now(UTC).isoformat(),
            }
            for attempt in range(args.max_retries + 1):
                result["attempts"] = attempt + 1
                try:
                    result.update(_score_one(row=row, llm=llm))
                    result["status"] = "completed"
                    result.pop("error", None)
                    break
                except Exception as exc:
                    result["error"] = str(exc)
                    if attempt < args.max_retries:
                        time.sleep(args.retry_delay_seconds * (attempt + 1))
            scores.append(result)
            output.write(json.dumps(result, ensure_ascii=True) + "\n")
            output.flush()
            print(json.dumps({"sample": index, "sample_id": row["sample_id"], "status": result["status"]}, ensure_ascii=True))
            if args.delay_seconds > 0 and index < len(rows):
                time.sleep(args.delay_seconds)

    summary = {
        "framework": "RAGAS",
        "ragas_version": _ragas_version(),
        "evaluator_provider": "Google Gemini",
        "evaluator_model": settings.gemini_chat_model,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        **_summary(scores),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    (args.run_dir / "summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    if summary["failed_samples"]:
        raise SystemExit("RAGAS scoring completed with failures; do not report this run as final.")


def _ragas_version() -> str:
    install_vertexai_import_compatibility()
    import ragas

    return str(ragas.__version__)


if __name__ == "__main__":
    main()
