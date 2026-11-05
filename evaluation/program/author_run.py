"""Journalled, budgeted Kimi draft generation from a reviewed blueprint."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from evaluation.program.artifacts import RunStore, StepKey
from evaluation.program.authoring import (
    AuthoringError, assemble_draft_dataset, author_prompt, parse_kimi_drafts,
)
from evaluation.program.blueprints import Blueprint
from evaluation.program.budget import BudgetError, BudgetLedger
from evaluation.program.budgeted_call import invoke_budgeted_role
from evaluation.program.config import EvaluationSettings, configuration_hash
from evaluation.program.contracts import CourseDataset, Question
from evaluation.program.pricing import PriceRegistry


def evidence_hash(packet: dict[str, Any]) -> str:
    encoded = json.dumps(packet, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AuthorRunResult:
    dataset: CourseDataset
    deficits: tuple[dict[str, int], ...]
    artifact: Path


def run_authoring_batches(
    *, store: RunStore, ledger: BudgetLedger, prices: PriceRegistry,
    settings: EvaluationSettings, provider: Any, blueprint: Blueprint,
    evidence_packet: dict[str, Any], dataset_id: str, version: str,
    retry_failed: bool = False, recover_interrupted: bool = False,
    fast_provider: Any | None = None,
) -> AuthorRunResult:
    """Run each Kimi batch at most once without an explicit retry choice.

    A completed step resumes from journalled validated questions. Failed or
    interrupted steps are never silently re-dispatched. All calls use the same
    ledger; this does not make other evaluation operations budget-safe.
    """
    if blueprint.course_id != evidence_packet.get("course_id"):
        raise AuthoringError("blueprint course differs from evidence packet")
    if not isinstance(dataset_id, str) or not dataset_id.strip() or not isinstance(version, str) or not version.strip():
        raise AuthoringError("dataset_id and version are required before authoring calls")
    used_splits: dict[str, str] = {}
    for request in blueprint.batches:
        for chunk_id in request.chunk_ids:
            prior = used_splits.get(chunk_id)
            if prior is not None and prior != request.split:
                raise AuthoringError(f"chunk {chunk_id} appears in both authoring splits")
            used_splits[chunk_id] = request.split
    if store.manifest["configuration_hash"] != configuration_hash(settings.public_snapshot()):
        raise AuthoringError("run configuration differs from the authoring settings")
    if store.manifest["dataset_hash"] != evidence_hash(evidence_packet):
        raise AuthoringError("run evidence packet differs from its frozen hash")
    batches: list[list[Question]] = []
    deficits: list[dict[str, int]] = []
    for number, request in enumerate(blueprint.batches, start=1):
        prompt, prompt_hash = author_prompt(evidence_packet, request)
        key = StepKey("author_batch", f"{blueprint.course_id}-{number:04d}")
        input_hash = sha256((prompt_hash + "\n" + settings.author_model + "\n"
                             + request.split).encode("utf-8")).hexdigest()
        step = store.begin_step(key, input_hash=input_hash, retry_failed=retry_failed,
                                recover_interrupted=recover_interrupted)
        if step.status == "completed":
            result = step.result
        else:
            messages = [{"role": "user", "content": prompt}]
            max_input_tokens = max(
                len(json.dumps(messages, ensure_ascii=False).encode("utf-8")),
                len(json.dumps(messages, ensure_ascii=True).encode("utf-8")),
            ) + 1024
            max_output_tokens = getattr(getattr(provider, "config", None), "max_output_tokens", None)
            if not isinstance(max_output_tokens, int) or max_output_tokens < 1:
                store.fail_step(key, input_hash=input_hash, attempt=step.attempts,
                                error_code="unbounded_output")
                raise BudgetError("Kimi provider must configure a positive output token limit")
            active_provider = provider
            if fast_provider is not None and request.split != "held_out":
                # Development batches run with a capped reasoning budget; the
                # held-out split keeps full reasoning for harder synthesis.
                active_provider = fast_provider
            try:
                billed = invoke_budgeted_role(settings, "author", active_provider, messages,
                    ledger=ledger, prices=prices, max_input_tokens=max_input_tokens,
                    max_output_tokens=max_output_tokens)
                questions, deficit = parse_kimi_drafts(evidence_packet, request, billed.result.text)
                result = {"questions": [q.model_dump(mode="json") for q in questions],
                          "deficit": deficit, "prompt_sha256": prompt_hash,
                          "call_id": billed.usage.call_id,
                          "provider": billed.usage.provider, "model": billed.usage.model,
                          "cost_usd": str(billed.cost.amount),
                          "usage": {"input_tokens": billed.usage.input_tokens,
                                    "output_tokens": billed.usage.output_tokens,
                                    "cached_input_tokens": billed.usage.cached_input_tokens}}
                store.complete_step(key, input_hash=input_hash, attempt=step.attempts, result=result)
            except Exception as exc:
                if store.get_step(key).status == "running":
                    code = ("budget_error" if isinstance(exc, BudgetError) else
                            "authoring_error" if isinstance(exc, AuthoringError) else "provider_error")
                    store.fail_step(key, input_hash=input_hash, attempt=step.attempts,
                                    error_code=code)
                raise
        batches.append([Question.model_validate(row) for row in result["questions"]])
        deficits.append(result["deficit"])
    dataset = assemble_draft_dataset(evidence_packet, batches, dataset_id=dataset_id,
                                     version=version)
    body = (json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n")
    target = store.path / "artifacts" / "draft_dataset.json"
    if target.exists():
        if target.read_text(encoding="utf-8") != body:
            raise AuthoringError("existing draft artifact differs from completed authoring steps")
    else:
        store.write_artifact("draft_dataset.json", body)
    return AuthorRunResult(dataset, tuple(deficits), target)
