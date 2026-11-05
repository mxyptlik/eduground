"""Plan or execute budgeted Kimi question authoring over a reviewed evidence packet.

Plan mode validates the blueprint and lists blockers without any provider call.
Execute mode requires the shared USD cap, the OpenRouter credential and a dated
sourced price registry; completed batches resume from the journal and every
attempt is recorded in the run budget ledger. Deficits are reported, not
filled with invented questions.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:  # Load local .env values into the process environment for credential checks.
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:  # pragma: no cover - dotenv is optional for plan mode
    pass

from evaluation.program.artifacts import RunStore, StepKey
from evaluation.program.author_run import evidence_hash, run_authoring_batches
from evaluation.program.blueprints import blueprint_plan, parse_blueprint
from evaluation.program.budget import BudgetLedger
from evaluation.program.config import EvaluationSettings, configuration_hash
from evaluation.program.pricing import PriceRegistry
from evaluation.program.providers import build_role_provider


def build_report(store: RunStore, blueprint, ledger: BudgetLedger) -> dict:
    batches = []
    total = Decimal(0)
    for number, request in enumerate(blueprint.batches, start=1):
        key = StepKey("author_batch", f"{blueprint.course_id}-{number:04d}")
        record = store.get_step(key)
        entry = {"batch": number, "split": request.split, "requested": request.count,
                 "status": record.status if record else "missing"}
        if record is not None and record.status == "completed":
            result = record.result or {}
            entry["questions"] = len(result.get("questions", []))
            entry["deficit"] = result.get("deficit")
            entry["cost_usd"] = result.get("cost_usd")
            entry["model"] = result.get("model")
            entry["provider"] = result.get("provider")
            entry["usage"] = result.get("usage")
            if result.get("cost_usd") is not None:
                total += Decimal(str(result["cost_usd"]))
        elif record is not None and record.status == "failed":
            entry["error"] = record.error_code
        batches.append(entry)
    state = ledger.state()
    return {
        "course_id": blueprint.course_id,
        "batches": batches,
        "known_cost_usd": str(total),
        "unknown_cost_attempts": state.unknown_attempts,
        "reserved_usd": str(state.reserved_usd),
        "settled_usd": str(state.settled_usd),
        "ledger_cap_usd": str(state.cap_usd),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-packet", type=Path, required=True)
    parser.add_argument("--blueprint", type=Path, required=True)
    parser.add_argument("--run-root", default=str(Path("evaluation") / "runs"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--prices", help="dated sourced price registry (required for --execute)")
    parser.add_argument("--ledger", help="reuse one shared pilot ledger file instead of a run-local one")
    parser.add_argument("--dataset-id")
    parser.add_argument("--version", default="draft-1")
    parser.add_argument("--resume")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--recover-interrupted", action="store_true")
    args = parser.parse_args()

    settings = EvaluationSettings()
    packet = json.loads(args.evidence_packet.read_text(encoding="utf-8"))
    blueprint = parse_blueprint(json.loads(args.blueprint.read_text(encoding="utf-8")), packet)
    plan = blueprint_plan(blueprint, packet)

    issues: list[str] = []
    if not settings.max_api_spend_usd:
        issues.append("set EDUGROUND_EVAL_MAX_API_SPEND_USD before live authoring")
    if not os.environ.get("CURRICULUM_TUTOR_OPENROUTER_API_KEY", "").strip():
        issues.append("missing credential environment variable (value not shown): CURRICULUM_TUTOR_OPENROUTER_API_KEY")
    report = {"plan_only": not args.execute, "course_id": blueprint.course_id,
              "batch_count": len(blueprint.batches), "plan": plan, "issues": issues,
              "execution_ready": not issues}
    if not args.execute:
        print(json.dumps(report, indent=2))
        return 0 if not issues else 2
    if issues:
        print(json.dumps({"execution_blocked": True, **report}, indent=2))
        return 2
    if not args.prices or not args.dataset_id:
        parser.error("--execute requires --prices and --dataset-id")
    prices = PriceRegistry.from_json(args.prices)

    run_root = Path(args.run_root)
    config_hash = configuration_hash(settings.public_snapshot())
    packet_hash = evidence_hash(packet)
    if args.resume:
        store = RunStore.open(run_root, args.resume, configuration_hash=config_hash, dataset_hash=packet_hash)
    else:
        store = RunStore.create(run_root, configuration_hash=config_hash, dataset_hash=packet_hash)
    ledger_path = Path(args.ledger) if args.ledger else store.path / "budget.sqlite3"
    ledger = BudgetLedger(ledger_path, settings.max_api_spend_usd)
    provider = build_role_provider(settings, "author")
    # Capped/disabled reasoning failed in production: development batches
    # exhausted their retries on quote fidelity (4 courses, 20+ consecutive
    # failures at cap=1024 vs 1-3 attempts to converge at full reasoning).
    # Full reasoning is the converged default; the env knob remains for
    # deliberate experiments.
    dev_reasoning_mode = os.environ.get("EDUGROUND_EVAL_AUTHOR_REASONING_DEV", "full").strip().lower()
    if dev_reasoning_mode == "off":
        fast_provider = build_role_provider(settings, "author", reasoning={"enabled": False})
    elif dev_reasoning_mode == "full":
        fast_provider = None
    else:
        fast_provider = build_role_provider(settings, "author", reasoning={"max_tokens": 1024})
    result = run_authoring_batches(
        store=store, ledger=ledger, prices=prices, settings=settings, provider=provider,
        fast_provider=fast_provider,
        blueprint=blueprint, evidence_packet=packet, dataset_id=args.dataset_id, version=args.version,
        retry_failed=args.retry_failed, recover_interrupted=args.recover_interrupted,
    )
    authoring_report = build_report(store, blueprint, ledger)
    body = json.dumps(authoring_report, indent=2, sort_keys=True) + "\n"
    target = store.path / "artifacts" / "authoring_report.json"
    if target.exists():
        if target.read_text(encoding="utf-8") != body:
            raise SystemExit("existing authoring report differs from completed journal steps")
    else:
        store.write_artifact("authoring_report.json", body)
    print(json.dumps({"run": str(store.path), "draft_artifact": str(result.artifact),
                      "questions": len(result.dataset.samples), "deficits": result.deficits,
                      "report": authoring_report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
