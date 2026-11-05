"""Controlled, resumable answer collection for the evaluation conditions.

Each item is collected in isolation: prompts are built from zero history and no
chat-memory store is involved, so an earlier question cannot influence a later
answer. The paired retrieval conditions ``no_rerank`` and ``rerank`` reuse one
frozen candidate pool and the same final count/token budget; ``no_retrieval``
performs no retrieval at all and ``reviewed_evidence`` uses only the evidence
accepted for that item.

Every step commits to the run journal before the next one starts, so a crash or
cancellation leaves a resumable run. Failed, interrupted, empty and
unknown-cost outcomes stay distinguishable in the results and summary.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from evaluation.program.artifacts import RunStore, StepKey
from evaluation.program.budget import BudgetError, BudgetLedger
from evaluation.program.collector import prompt_messages, snapshot_candidates
from evaluation.program.conditions import (
    Candidate,
    RerankOrder,
    build_context,
    no_retrieval_context,
    reviewed_evidence_context,
)
from evaluation.program.config import EvaluationSettings, configuration_hash
from evaluation.program.contracts import CourseDataset, Question
from evaluation.program.timing import StageTimer


CONDITIONS: tuple[str, ...] = ("no_retrieval", "no_rerank", "rerank", "reviewed_evidence")
RETRIEVAL_CONDITIONS = frozenset({"no_rerank", "rerank"})
GROUNDED_CONDITIONS = frozenset({"no_rerank", "rerank", "reviewed_evidence"})


class RunnerError(ValueError):
    """The run configuration or journal state cannot support collection."""


class RetrievalFailure(RuntimeError):
    """Retrieval failed before candidates existed; the code stays inspectable."""

    def __init__(self, code: str, message: str = "") -> None:
        if not isinstance(code, str) or not code or not code.replace("_", "").isalnum() or not code.islower():
            raise ValueError("retrieval failure code must be a short lowercase identifier")
        super().__init__(message or code)
        self.code = code


class GenerationFailure(RuntimeError):
    """Answer generation failed with a caller-supplied outcome code."""

    def __init__(self, code: str, message: str = "") -> None:
        if not isinstance(code, str) or not code or not code.replace("_", "").isalnum() or not code.islower():
            raise ValueError("generation failure code must be a short lowercase identifier")
        super().__init__(message or code)
        self.code = code


class _StepFailure(RuntimeError):
    """Internal marker carrying a journal-safe failure code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GenerationOutcome:
    """Normalized result of one budgeted answer-model call."""

    text: str
    model_id: str | None = None
    usage: Mapping[str, Any] | None = None
    cost_usd: str | None = None
    reserved_usd: str | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("generation text must be a string")


@dataclass
class RunOutcome:
    run_id: str
    run_path: Path
    expected_steps: int
    completed: int
    failed: int
    missing: int
    interrupted: int
    reused_completed: int
    skipped_failed: int
    cancelled: bool
    by_condition: dict[str, dict[str, int]]
    failures: list[dict[str, str]]
    version_check: dict[str, int]
    unknown_cost_calls: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_path": str(self.run_path),
            "expected_steps": self.expected_steps,
            "completed": self.completed,
            "failed": self.failed,
            "missing": self.missing,
            "interrupted": self.interrupted,
            "reused_completed": self.reused_completed,
            "skipped_failed": self.skipped_failed,
            "cancelled": self.cancelled,
            "by_condition": self.by_condition,
            "failures": self.failures,
            "version_check": self.version_check,
            "unknown_cost_calls": self.unknown_cost_calls,
        }


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _item_id(course_id: str, sample_id: str) -> str:
    return f"{course_id}:{sample_id}"


def configuration_digest(settings: EvaluationSettings) -> str:
    return configuration_hash(settings.public_snapshot())


def dataset_digest(dataset: CourseDataset) -> str:
    return _hash(dataset.model_dump(mode="json"))


def _validate_conditions(conditions: Sequence[str]) -> tuple[str, ...]:
    chosen = tuple(conditions)
    if not chosen or len(set(chosen)) != len(chosen) or set(chosen) - set(CONDITIONS):
        raise RunnerError("provide unique known conditions")
    return chosen


def _candidate_payload(candidate: Candidate) -> dict[str, Any]:
    return {
        "chunk_id": candidate.chunk_id,
        "source_id": candidate.source_id,
        "text": candidate.text,
        "token_count": candidate.token_count,
        "initial_score": candidate.initial_score,
        "rank_before": candidate.rank_before,
        "locator": candidate.locator,
        "source_version_id": candidate.source_version_id,
    }


def _candidates_from_payload(rows: Sequence[Mapping[str, Any]]) -> tuple[Candidate, ...]:
    try:
        return tuple(
            Candidate(
                chunk_id=str(row["chunk_id"]),
                source_id=str(row["source_id"]),
                text=str(row["text"]),
                token_count=int(row["token_count"]),
                initial_score=float(row["initial_score"]),
                rank_before=int(row["rank_before"]),
                locator=row.get("locator"),
                source_version_id=row.get("source_version_id"),
            )
            for row in rows
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RunnerError("journalled candidates are invalid") from exc


@dataclass
class _RetrievalState:
    """Either a completed snapshot or the failure code its conditions inherit."""

    result: dict[str, Any] | None = None
    failure_code: str | None = None


@dataclass
class _Counters:
    reused_completed: int = 0
    skipped_failed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_reused(self) -> None:
        with self._lock:
            self.reused_completed += 1

    def add_skipped_failed(self) -> None:
        with self._lock:
            self.skipped_failed += 1


def run_collection(
    dataset: CourseDataset,
    settings: EvaluationSettings,
    store: RunStore,
    *,
    conditions: Sequence[str],
    generate: Callable[[list[dict[str, str]]], GenerationOutcome],
    grounded_system_prompt: str,
    retrieve: Callable[[str], tuple[Sequence[Any], Mapping[str, str] | None]] | None = None,
    rerank: Callable[[str, Sequence[Candidate]], RerankOrder] | None = None,
    workers: int = 1,
    retry_budget: int = 2,
    retry_failed: bool = False,
    recover_interrupted: bool = False,
    should_stop: Callable[[], bool] | None = None,
    ledger: BudgetLedger | None = None,
) -> RunOutcome:
    """Collect answers for every item and condition, resuming what already ran.

    ``retrieve`` returns ``(records, current_source_versions)``; the version map
    may be ``None`` when the caller cannot verify frozen-source versions, in
    which case results are marked ``unchecked`` instead of claiming verification.

    Step input hashes bind the run to dataset and configuration identity, not to
    a particular candidate pool: changing the corpus, question or configuration
    requires a new run instead of silently reusing old steps. ``retry_failed``
    retries recorded failures up to ``retry_budget`` attempts; ``budget_blocked``
    failures are never retried in the same invocation because the ledger is
    blocking all new reservations.
    """
    chosen = _validate_conditions(conditions)
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 16:
        raise RunnerError("workers must be between 1 and 16")
    if isinstance(retry_budget, bool) or not isinstance(retry_budget, int) or retry_budget < 1:
        raise RunnerError("retry_budget must be positive")
    needs_retrieval = bool(set(chosen) & RETRIEVAL_CONDITIONS)
    if needs_retrieval and retrieve is None:
        raise RunnerError("retrieval conditions require a retrieve callable")
    if "rerank" in chosen and rerank is None:
        raise RunnerError("rerank condition requires the pinned reranker callable")
    if set(chosen) & GROUNDED_CONDITIONS and not grounded_system_prompt.strip():
        raise RunnerError("grounded conditions require the product system prompt")

    items = tuple(dataset.samples)
    if not items:
        raise RunnerError("dataset has no items")
    allowed_sources = frozenset(source.source_id for source in dataset.source_manifest)
    config_digest = configuration_digest(settings)
    dataset_hash = dataset_digest(dataset)
    counters = _Counters()

    expected_keys = [
        StepKey("answer", _item_id(dataset.course_id, sample.sample_id), condition)
        for sample in items for condition in chosen
    ]
    if needs_retrieval:
        expected_keys.extend(
            StepKey("retrieval", _item_id(dataset.course_id, sample.sample_id)) for sample in items
        )
    if not recover_interrupted:
        stale = [key for key in expected_keys
                 if (record := store.get_step(key)) is not None and record.status == "running"]
        if stale:
            raise RunnerError(
                f"{len(stale)} step(s) look interrupted; rerun with recover_interrupted=True after checking the journal"
            )

    def run_step(key: StepKey, input_hash: str, execute: Callable[[], Any]) -> Any | None:
        existing = store.get_step(key)
        if existing is not None and existing.status == "completed":
            if existing.input_hash != input_hash:
                raise RunnerError(f"completed step input changed: {key.item_id}/{key.metric_id or key.kind}")
            counters.add_reused()
            return existing.result
        begin_kwargs: dict[str, bool] = {}
        if existing is not None and existing.status == "failed":
            if not retry_failed or existing.attempts >= retry_budget or existing.error_code == "budget_blocked":
                counters.add_skipped_failed()
                return None
            begin_kwargs["retry_failed"] = True
        elif existing is not None and existing.status == "running":
            begin_kwargs["recover_interrupted"] = True
        record = store.begin_step(key, input_hash=input_hash, **begin_kwargs)
        try:
            result = execute()
        except _StepFailure as failure:
            store.fail_step(key, input_hash=input_hash, attempt=record.attempts, error_code=failure.code)
            return None
        except BudgetError:
            store.fail_step(key, input_hash=input_hash, attempt=record.attempts, error_code="budget_blocked")
            return None
        except BaseException:
            store.fail_step(key, input_hash=input_hash, attempt=record.attempts, error_code="internal_error")
            raise
        store.complete_step(key, input_hash=input_hash, attempt=record.attempts, result=result)
        return result

    def run_retrieval(sample: Question) -> _RetrievalState:
        key = StepKey("retrieval", _item_id(dataset.course_id, sample.sample_id))
        input_hash = _hash({
            "step": "retrieval", "item": key.item_id, "question": sample.question,
            "notebook_id": dataset.notebook_id, "sources": sorted(allowed_sources),
            "candidate_k": settings.candidate_k, "configuration_hash": config_digest,
            "dataset_hash": dataset_hash,
        })

        def execute() -> dict[str, Any]:
            timer = StageTimer()
            try:
                with timer.measure("retrieval"):
                    records, version_map = retrieve(sample.question)  # type: ignore[misc]
                    candidates = snapshot_candidates(records, current_source_versions=version_map)
            except BudgetError:
                raise
            except RetrievalFailure as failure:
                raise _StepFailure(failure.code) from failure
            except ValueError as exc:
                raise _StepFailure("retrieval_validation_failed") from exc
            except Exception as exc:
                raise _StepFailure("retrieval_error") from exc
            outside = sorted({candidate.source_id for candidate in candidates if candidate.source_id not in allowed_sources})
            if outside:
                raise _StepFailure("candidate_outside_frozen_sources")
            return {
                "version_check": "verified" if version_map is not None else "unchecked",
                "candidates": [_candidate_payload(candidate) for candidate in candidates],
                "timings_ms": timer.summary(),
            }

        stored = run_step(key, input_hash, execute)
        if stored is not None:
            return _RetrievalState(result=stored)
        record = store.get_step(key)
        if record is not None and record.status == "failed":
            return _RetrievalState(failure_code=record.error_code or "retrieval_error")
        raise RunnerError("retrieval step is neither completed nor failed; resume the run")

    def run_answer(sample: Question, condition: str, retrieval: _RetrievalState | None) -> None:
        item = _item_id(dataset.course_id, sample.sample_id)
        key = StepKey("answer", item, condition)
        candidates: tuple[Candidate, ...] = ()
        if condition in RETRIEVAL_CONDITIONS:
            if retrieval is not None and retrieval.result is not None:
                candidates = _candidates_from_payload(retrieval.result["candidates"])
        input_hash = _hash({
            "step": "answer", "item": item, "condition": condition,
            "question": sample.question, "dataset_hash": dataset_hash,
            "configuration_hash": config_digest, "final_k": settings.final_k,
            "max_context_tokens": settings.max_context_tokens,
        })

        def fail_now() -> None:
            failure_code = retrieval.failure_code if retrieval is not None else "retrieval_error"
            raise _StepFailure(failure_code or "retrieval_error")

        if condition in RETRIEVAL_CONDITIONS and (retrieval is None or retrieval.failure_code is not None):
            run_step(key, input_hash, fail_now)
            return

        def execute() -> dict[str, Any]:
            timer = StageTimer()
            rerank_status = "not_applicable"
            rerank_meta: dict[str, Any] | None = None
            rerank_detail: dict[str, dict[str, Any]] = {}
            try:
                if condition == "no_retrieval":
                    context = no_retrieval_context()
                elif condition == "reviewed_evidence":
                    context = reviewed_evidence_context(sample.evidence)
                elif condition == "no_rerank":
                    context = build_context(
                        "no_rerank", candidates, [c.chunk_id for c in candidates],
                        final_k=settings.final_k, max_context_tokens=settings.max_context_tokens,
                        rerank_status="disabled",
                    )
                    rerank_status = "not_configured"
                else:
                    try:
                        with timer.measure("rerank"):
                            order = rerank(sample.question, candidates)  # type: ignore[misc]
                        if order.status not in {"succeeded", "partial"}:
                            raise ValueError("reranker outcome must be succeeded or partial")
                        rerank_status = order.status
                        rerank_meta = {
                            "response_model": order.response_model,
                            "request_id": order.request_id,
                            "usage": dict(order.usage) if order.usage else None,
                            "truncated_passage_count": order.truncated_passage_count,
                            "ranked_coverage": order.ranked_coverage,
                        }
                        ranked_ids = list(order.chunk_ids)
                        scores = order.scores or (None,) * len(order.chunk_ids)
                        rerank_detail = {
                            chunk_id: {"rank_after": rank, "rerank_score": score}
                            for rank, (chunk_id, score) in enumerate(zip(order.chunk_ids, scores), start=1)
                        }
                    except Exception:
                        rerank_status = "failed"
                        ranked_ids = [c.chunk_id for c in candidates]
                    context = build_context(
                        "rerank", candidates, ranked_ids,
                        final_k=settings.final_k, max_context_tokens=settings.max_context_tokens,
                        rerank_status=rerank_status,
                    )
                messages = prompt_messages(context, question=sample.question,
                                           grounded_system_prompt=grounded_system_prompt)
            except (RetrievalFailure, GenerationFailure, _StepFailure):
                raise
            except ValueError as exc:
                raise _StepFailure("answer_contract_error") from exc
            try:
                with timer.measure("generation"):
                    outcome = generate(messages)
            except BudgetError:
                raise
            except GenerationFailure as failure:
                raise _StepFailure(failure.code) from failure
            except Exception as exc:
                raise _StepFailure("generation_error") from exc
            if not isinstance(outcome, GenerationOutcome):
                raise _StepFailure("generation_contract_error")
            retrieval_status = (
                "not_requested" if condition not in RETRIEVAL_CONDITIONS
                else ("ok" if candidates else "empty")
            )
            if condition in RETRIEVAL_CONDITIONS and retrieval is not None and retrieval.result is not None:
                version_check = str(retrieval.result.get("version_check") or "unchecked")
            else:
                version_check = "not_applicable"
            return {
                "item_id": item,
                "course_id": dataset.course_id,
                "condition": condition,
                "split": str(sample.split),
                "answerable": sample.answerable,
                "outcome": "completed",
                "retrieval_status": retrieval_status,
                "rerank_status": rerank_status,
                "rerank": rerank_meta,
                "candidates": [
                    {**_candidate_payload(candidate), **rerank_detail.get(candidate.chunk_id, {})}
                    for candidate in candidates
                ],
                "final_chunk_ids": list(context.final_chunk_ids),
                "omitted_chunk_ids": list(context.omitted_chunk_ids),
                "exact_context": context.exact_context,
                "prompt_messages": messages,
                "answer_text": outcome.text,
                "answer_model": outcome.model_id,
                "answer_request_id": outcome.request_id,
                "answer_usage": dict(outcome.usage) if outcome.usage is not None else None,
                "answer_cost_usd": outcome.cost_usd,
                "reserved_usd": outcome.reserved_usd,
                "timings_ms": timer.summary(),
                "version_check": version_check,
            }

        run_step(key, input_hash, execute)

    def process_item(sample: Question) -> None:
        if should_stop and should_stop():
            return
        retrieval: _RetrievalState | None = None
        if needs_retrieval:
            retrieval = run_retrieval(sample)
        for condition in chosen:
            if should_stop and should_stop():
                return
            run_answer(sample, condition, retrieval)

    if workers == 1:
        for sample in items:
            if should_stop and should_stop():
                break
            process_item(sample)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(process_item, items))

    cancelled = bool(should_stop and should_stop())
    return summarize_run(store, dataset, chosen, counters=counters, cancelled=cancelled, ledger=ledger)


def summarize_run(
    store: RunStore,
    dataset: CourseDataset,
    conditions: Sequence[str],
    *,
    counters: _Counters | None = None,
    cancelled: bool = False,
    ledger: BudgetLedger | None = None,
) -> RunOutcome:
    """Reconcile every expected item/condition step from the journal."""
    chosen = _validate_conditions(conditions)
    needs_retrieval = bool(set(chosen) & RETRIEVAL_CONDITIONS)
    by_condition: dict[str, dict[str, int]] = {
        condition: {"completed": 0, "failed": 0, "missing": 0, "interrupted": 0} for condition in chosen
    }
    failures: list[dict[str, str]] = []
    version_check = {"verified": 0, "unchecked": 0, "not_applicable": 0}
    completed = failed = missing = interrupted = 0
    expected = 0
    for sample in dataset.samples:
        item = _item_id(dataset.course_id, sample.sample_id)
        if needs_retrieval:
            expected += 1
            record = store.get_step(StepKey("retrieval", item))
            if record is None:
                missing += 1
            elif record.status == "failed":
                failed += 1
                failures.append({"step": f"{item}:retrieval", "error": record.error_code or "retrieval_error"})
            elif record.status == "running":
                interrupted += 1
            elif record.status == "completed":
                completed += 1
        for condition in chosen:
            expected += 1
            record = store.get_step(StepKey("answer", item, condition))
            bucket = by_condition[condition]
            if record is None:
                missing += 1
                bucket["missing"] += 1
            elif record.status == "failed":
                failed += 1
                bucket["failed"] += 1
                failures.append({"step": f"{item}:{condition}", "error": record.error_code or "unknown"})
            elif record.status == "running":
                interrupted += 1
                bucket["interrupted"] += 1
            elif record.status == "completed":
                completed += 1
                bucket["completed"] += 1
                check = (record.result or {}).get("version_check")
                if check in version_check:
                    version_check[check] += 1
    unknown_cost_calls = ledger.state().unknown_attempts if ledger is not None else None
    return RunOutcome(
        run_id=store.manifest["run_id"],
        run_path=store.path,
        expected_steps=expected,
        completed=completed,
        failed=failed,
        missing=missing,
        interrupted=interrupted,
        reused_completed=counters.reused_completed if counters else 0,
        skipped_failed=counters.skipped_failed if counters else 0,
        cancelled=cancelled,
        by_condition=by_condition,
        failures=failures,
        version_check=version_check,
        unknown_cost_calls=unknown_cost_calls,
    )


def _write_if_new_or_identical(store: RunStore, name: str, data: bytes) -> Path:
    path = store.path / "artifacts" / Path(*name.split("/"))
    if path.exists():
        if path.read_bytes() == data:
            return path
        raise RunnerError(f"artifact already exists with different content: {name}")
    return store.write_artifact(name, data)


def materialize_run(
    store: RunStore, dataset: CourseDataset, *, conditions: Sequence[str],
    ledger: BudgetLedger | None = None,
) -> dict[str, Path]:
    """Publish stable JSONL/JSON views of a finished collection journal."""
    chosen = _validate_conditions(conditions)
    answers: list[str] = []
    retrievals: list[str] = []
    for sample in dataset.samples:
        item = _item_id(dataset.course_id, sample.sample_id)
        if set(chosen) & RETRIEVAL_CONDITIONS:
            record = store.get_step(StepKey("retrieval", item))
            if record is not None and record.status == "completed":
                retrievals.append(json.dumps({"item_id": item, **(record.result or {})},
                                             sort_keys=True, ensure_ascii=False, allow_nan=False))
        for condition in chosen:
            record = store.get_step(StepKey("answer", item, condition))
            if record is not None and record.status == "completed":
                answers.append(json.dumps(record.result, sort_keys=True, ensure_ascii=False, allow_nan=False))
    outcome = summarize_run(store, dataset, chosen, ledger=ledger)
    written: dict[str, Path] = {}
    written["answers.jsonl"] = _write_if_new_or_identical(
        store, "answers.jsonl", ("\n".join(answers) + "\n").encode("utf-8"))
    if retrievals:
        written["retrievals.jsonl"] = _write_if_new_or_identical(
            store, "retrievals.jsonl", ("\n".join(retrievals) + "\n").encode("utf-8"))
    written["failures.json"] = _write_if_new_or_identical(
        store, "failures.json", (json.dumps(outcome.failures, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    written["summary.json"] = _write_if_new_or_identical(
        store, "summary.json", (json.dumps(outcome.to_json(), indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return written
