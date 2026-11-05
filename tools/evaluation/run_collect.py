"""Controlled answer collection over a frozen reviewed release.

Plan mode is read-only and reports blockers (no provider calls). Execute mode
requires a frozen release directory produced by the review freeze step, a
dated sourced price registry, credentials in the environment (never printed),
and the explicit shared USD cap. Every outbound attempt goes through the
budget ledger; stop with Ctrl+C leaves a resumable run.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:  # Load local .env values into the process environment for credential checks.
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:  # pragma: no cover - dotenv is optional for plan mode
    pass

from evaluation.program.artifacts import RunStore
from evaluation.program.budget import BudgetLedger
from evaluation.program.config import EvaluationSettings
from evaluation.program.contracts import CourseDataset, adapt_legacy_v1
from evaluation.program.live import build_live_collection, collection_preflight
from evaluation.program.pricing import PriceRegistry
from evaluation.program.runner import (
    CONDITIONS,
    RETRIEVAL_CONDITIONS,
    configuration_digest,
    dataset_digest,
    materialize_run,
    run_collection,
)


def _load_release(path: Path) -> CourseDataset:
    dataset_path = path / "dataset.json" if path.is_dir() else path
    if not dataset_path.is_file():
        raise SystemExit(f"release dataset not found: {dataset_path}")
    raw = dataset_path.read_text(encoding="utf-8")
    checksum_path = dataset_path.with_name("dataset.sha256")
    if checksum_path.is_file():
        expected = checksum_path.read_text(encoding="ascii").strip()
        if sha256(raw.encode("utf-8")).hexdigest() != expected:
            raise SystemExit("release dataset.sha256 does not match dataset.json")
    return CourseDataset.model_validate(json.loads(raw))


def _load_draft(path: Path) -> CourseDataset:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and payload.get("schema_version") == 2:
        return CourseDataset.model_validate(payload)
    return adapt_legacy_v1(payload, course_id=path.stem)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--release", help="frozen reviewed release directory (dataset.json + dataset.sha256)")
    source.add_argument("--dataset", help="draft dataset JSON for plan mode only")
    parser.add_argument("--condition", action="append", required=True, choices=sorted(CONDITIONS),
                        help="experimental condition; repeat as needed")
    parser.add_argument("--execute", action="store_true", help="make real budgeted calls")
    parser.add_argument("--prices", help="dated sourced price registry JSON (required for --execute)")
    parser.add_argument("--ledger", help="reuse one shared pilot ledger file instead of a run-local one")
    parser.add_argument("--run-root", default=str(Path("evaluation") / "runs"))
    parser.add_argument("--resume", help="existing run ID under --run-root")
    parser.add_argument("--recover-interrupted", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-budget", type=int, default=2)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    conditions = tuple(dict.fromkeys(args.condition))
    need_retrieval = bool(set(conditions) & RETRIEVAL_CONDITIONS)
    need_rerank = "rerank" in conditions
    settings = EvaluationSettings()

    if args.execute and args.dataset:
        parser.error("--execute requires a frozen --release; drafts may only be planned")
    dataset = _load_release(Path(args.release)) if args.release else _load_draft(Path(args.dataset))

    issues = collection_preflight(settings, need_retrieval=need_retrieval, need_rerank=need_rerank)
    item_count = len(dataset.samples)
    plan = {
        "course_id": dataset.course_id,
        "dataset_id": dataset.dataset_id,
        "item_count": item_count,
        "conditions": list(conditions),
        "answer_calls_lower_bound": item_count * len(conditions),
        "retrieval_calls_lower_bound": item_count if need_retrieval else 0,
        "rerank_calls_lower_bound": item_count if need_rerank else 0,
        "max_api_spend_usd": settings.max_api_spend_usd,
        "configuration_hash": configuration_digest(settings),
        "dataset_hash": dataset_digest(dataset),
        "issues": issues,
        "execution_ready": not issues,
    }
    if not args.execute:
        print(json.dumps({"plan_only": True, **plan}, indent=2))
        return 0 if not issues else 2
    if issues:
        print(json.dumps({"execution_blocked": True, **plan}, indent=2))
        return 2
    if not args.prices:
        parser.error("--execute requires --prices with a dated sourced price registry")
    prices = PriceRegistry.from_json(args.prices)

    run_root = Path(args.run_root)
    config_hash = configuration_digest(settings)
    if args.resume:
        store = RunStore.open(run_root, args.resume, configuration_hash=config_hash,
                              dataset_hash=dataset_digest(dataset))
    else:
        store = RunStore.create(run_root, configuration_hash=config_hash,
                                dataset_hash=dataset_digest(dataset))
    ledger_path = Path(args.ledger) if args.ledger else store.path / "budget.sqlite3"
    ledger = BudgetLedger(ledger_path, settings.max_api_spend_usd)
    runtime = build_live_collection(settings, dataset, ledger=ledger, prices=prices,
                                    verify_versions=True, need_retrieval=need_retrieval,
                                    need_rerank=need_rerank)
    try:
        outcome = run_collection(
            dataset, settings, store, conditions=conditions,
            retrieve=runtime.retrieve if need_retrieval else None,
            rerank=runtime.rerank if need_rerank else None,
            generate=runtime.generate, grounded_system_prompt=runtime.grounded_system_prompt,
            workers=args.workers, retry_budget=args.retry_budget,
            retry_failed=args.retry_failed, recover_interrupted=args.recover_interrupted,
            ledger=ledger,
        )
    finally:
        runtime.close()
    written = materialize_run(store, dataset, conditions=conditions, ledger=ledger)
    report = outcome.to_json() | {"artifacts": {name: str(path) for name, path in written.items()}}
    print(json.dumps(report, indent=2))
    return 0 if outcome.failed == 0 and outcome.missing == 0 and not outcome.cancelled else 1


if __name__ == "__main__":
    raise SystemExit(main())
