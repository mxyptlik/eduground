"""Score a completed collection run with selectable metric groups.

Deterministic retrieval and citation checks run offline from the frozen run
journal. RAGAS metrics require the installed RAGAS environment, a DeepSeek
judge and the shared budget ledger with dated prices; they never run unless
explicitly selected. Derived score tables are atomically replaced; the raw
journal and run artifacts are never modified.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:  # Load local .env values into the process environment for credential checks.
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:  # pragma: no cover - dotenv is optional for offline scoring
    pass

from evaluation.program.artifacts import RunStore
from evaluation.program.budget import BudgetLedger
from evaluation.program.config import EvaluationSettings
from evaluation.program.contracts import CourseDataset
from evaluation.program.metrics.ragas import RAGAS_METRICS
from evaluation.program.pricing import PriceRegistry
from evaluation.program.scoring import (
    citation_checks_for_run,
    score_ragas_for_run,
    score_retrieval_for_run,
)


def _load_release(path: Path) -> CourseDataset:
    dataset_path = path / "dataset.json" if path.is_dir() else path
    return CourseDataset.model_validate(json.loads(dataset_path.read_text(encoding="utf-8")))


def _replace_json(path: Path, value) -> None:
    """Derived reporting only: write atomically and replace prior derived tables."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="run directory containing journal.sqlite3")
    parser.add_argument("--release", required=True, help="frozen reviewed release directory or dataset JSON")
    parser.add_argument("--condition", action="append", required=True)
    parser.add_argument("--metric", action="append", default=[], help="metric ID; repeat as needed")
    parser.add_argument("--k", action="append", type=int, default=[], help="retrieval cutoff; repeat (default 1,3,5,10)")
    parser.add_argument("--ragas-limit", type=int, default=None, help="bound new judge calls this invocation")
    parser.add_argument("--concurrency", type=int, default=2, help="parallel judge calls (1-16)")
    parser.add_argument("--prices", help="dated sourced price registry (required for RAGAS metrics)")
    parser.add_argument("--ledger", help="reuse one shared pilot ledger file instead of a run-local one")
    parser.add_argument("--recover-interrupted", action="store_true")
    parser.add_argument("--retry-failed", action="store_true",
                        help="re-dispatch previously failed judge metric steps")
    parser.add_argument("--allow-unverified-score", action="store_true",
                        help="score missing answers as explicit rows instead of failing loudly")
    args = parser.parse_args()

    run_path = Path(args.run)
    store = RunStore.open(run_path.parent, run_path.name,
                          configuration_hash=store_hash(run_path, "configuration_hash"),
                          dataset_hash=store_hash(run_path, "dataset_hash"))
    dataset = _load_release(Path(args.release))
    conditions = tuple(dict.fromkeys(args.condition))
    k_values = tuple(args.k or (1, 3, 5, 10))

    selected = list(dict.fromkeys(args.metric))
    ragas_metrics = [m for m in selected if m in RAGAS_METRICS]
    if ragas_metrics and not args.prices:
        parser.error("RAGAS metrics require --prices with a dated sourced price registry")

    output: dict[str, object] = {}
    retrieval_rows = [asdict(row) for row in score_retrieval_for_run(
        dataset, store, conditions=conditions, k_values=k_values)]
    _replace_json(run_path / "artifacts" / "scoring" / "retrieval.json", retrieval_rows)
    output["retrieval_rows"] = len(retrieval_rows)

    citation_rows = [asdict(row) for row in citation_checks_for_run(
        dataset, store, conditions=conditions)]
    _replace_json(run_path / "artifacts" / "scoring" / "citations.json", citation_rows)
    output["citation_rows"] = len(citation_rows)
    output["citation_status_counts"] = _counts(row["status"] for row in citation_rows)

    if ragas_metrics:
        settings = EvaluationSettings()
        if settings.max_api_spend_usd is None:
            parser.error("set EDUGROUND_EVAL_MAX_API_SPEND_USD before scoring with the judge")
        ledger = BudgetLedger(Path(args.ledger) if args.ledger else run_path / "budget.sqlite3",
                              settings.max_api_spend_usd)
        prices = PriceRegistry.from_json(args.prices)
        api_key = os.environ.get("CURRICULUM_TUTOR_OPENROUTER_API_KEY", "").strip()
        if not api_key:
            parser.error("CURRICULUM_TUTOR_OPENROUTER_API_KEY is required for the RAGAS judge")
        from evaluation.program.ragas_judge import build_deepseek_ragas_judge

        judge = build_deepseek_ragas_judge(
            settings, api_key=api_key, ledger=ledger, prices=prices,
            max_input_tokens=32768, max_output_tokens=12288,
        )
        embeddings = None
        if "answer_relevancy" in ragas_metrics:
            from evaluation.program.budgeted_embeddings import build_ragas_budgeted_embeddings

            embeddings, _requester = build_ragas_budgeted_embeddings(
                settings, ledger=ledger, prices=prices,
            )
        ragas_rows = [asdict(row) for row in score_ragas_for_run(
            dataset, store, conditions=conditions, metric_ids=ragas_metrics,
            judge_llm=judge.llm, embeddings=embeddings, limit=args.ragas_limit,
            recover_interrupted=args.recover_interrupted, retry_failed=args.retry_failed,
            concurrency=args.concurrency)]
        _replace_json(run_path / "artifacts" / "scoring" / "ragas.json", ragas_rows)
        output["ragas_rows"] = len(ragas_rows)
        output["ragas_status_counts"] = _counts(row["status"] for row in ragas_rows)

    summary = {"run": str(run_path), "conditions": list(conditions), "metrics": selected, **output}
    _replace_json(run_path / "artifacts" / "scoring" / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


def _counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def store_hash(run_path: Path, key: str) -> str:
    manifest = json.loads((run_path / "manifest.json").read_text(encoding="utf-8"))
    return str(manifest[key])


if __name__ == "__main__":
    raise SystemExit(main())
