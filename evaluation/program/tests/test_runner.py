"""Offline tests for the controlled answer collector runner.

These use fake providers and a temp run journal; no provider or database is
touched. They cover condition isolation, matched candidate pools, the outcome
taxonomy, resume behaviour, cancellation, version checks and materialization.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from evaluation.program.artifacts import RunStore, StepKey
from evaluation.program.budget import BudgetError
from evaluation.program.conditions import RerankOrder
from evaluation.program.config import EvaluationSettings
from evaluation.program.contracts import CourseDataset
from evaluation.program.runner import (
    GenerationFailure,
    GenerationOutcome,
    RetrievalFailure,
    RunnerError,
    materialize_run,
    run_collection,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def make_settings(**overrides) -> EvaluationSettings:
    values = dict(
        answer_model="gemini-3.5-flash",
        author_model="moonshotai/kimi-k2.6",
        judge_model="deepseek/deepseek-v4.1-flash",
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-large",
        relevance_embedding_provider="openrouter",
        relevance_embedding_model="openai/text-embedding-3-large",
        candidate_k=4,
        final_k=2,
        max_context_tokens=128,
        max_api_spend_usd=10,
    )
    values.update(overrides)
    return EvaluationSettings(**values)


def make_dataset(*, items: int = 2, include_unanswerable: bool = False) -> CourseDataset:
    samples = []
    for index in range(1, items + 1):
        samples.append({
            "sample_id": f"q{index}",
            "question": f"What is the answer to question {index}?",
            "reference_answer": f"Reference answer {index}",
            "evidence": [{"source_id": "s1", "chunk_id": f"c{index}", "quote": f"quote {index}", "locator": "page 1"}],
        })
    if include_unanswerable:
        samples.append({"sample_id": "q-unanswerable", "question": "What is not covered anywhere?",
                        "answerable": False})
    return CourseDataset(
        dataset_id="csc1-v1", version="1", course_id="CSC1", notebook_id="n1",
        source_manifest=[{"source_id": "s1", "source_label": "Lecture 1", "notebook_id": "n1", "sha256": HASH_A}],
        samples=samples,
    )


def record(chunk_id: str, rank: int, *, version: str | None = "v1") -> SimpleNamespace:
    chunk = SimpleNamespace(id=chunk_id, source_id="s1", source_version_id=version,
                            text=f"Evidence {chunk_id} " + "x" * 20, token_count=8,
                            start_page=1, start_slide=None)
    source = SimpleNamespace(id="s1")
    return SimpleNamespace(chunk=chunk, source=source, score=1.0 / rank,
                           rank_before=rank, rank_after=rank, rerank_score=None)


def make_store(tmp_path: Path, name: str = "runs") -> RunStore:
    return RunStore.create(tmp_path / name, configuration_hash=HASH_A, dataset_hash=HASH_B)


def success_generator(calls: list, text_prefix: str = "answer"):
    def generate(messages):
        calls.append(messages)
        return GenerationOutcome(text=f"{text_prefix}-{len(calls)}", model_id="gemini-3.5-flash",
                                 cost_usd="0.0001", usage={"promptTokenCount": 10})
    return generate


def answers(store: RunStore, dataset: CourseDataset, conditions) -> dict:
    results = {}
    for sample in dataset.samples:
        for condition in conditions:
            record_ = store.get_step(StepKey("answer", f"{dataset.course_id}:{sample.sample_id}", condition))
            results[(sample.sample_id, condition)] = record_.result if record_ else None
    return results


def test_all_conditions_share_candidate_pool_and_isolate_prompts(tmp_path):
    dataset = make_dataset(items=3)
    settings = make_settings()
    store = make_store(tmp_path)
    conditions = ("no_rerank", "rerank", "no_retrieval")
    retrieval_calls, generation_calls = [], []

    def retrieve(question):
        retrieval_calls.append(question)
        return [record(f"c{len(retrieval_calls)}{i}", i, version="v1") for i in range(1, 5)], {"s1": "v1"}

    def rerank(question, candidates):
        return RerankOrder(tuple(reversed([c.chunk_id for c in candidates])), "succeeded")

    outcome = run_collection(
        dataset, settings, store, conditions=conditions,
        retrieve=retrieve, rerank=rerank,
        generate=success_generator(generation_calls),
        grounded_system_prompt="Use only the retrieved evidence.",
    )
    assert outcome.completed == outcome.expected_steps == 3 * 3 + 3
    assert outcome.failed == 0 and not outcome.cancelled
    assert len(retrieval_calls) == 3, "one frozen candidate pool per item"
    assert len(generation_calls) == 9
    assert outcome.version_check == {"verified": 6, "unchecked": 0, "not_applicable": 3}

    results = answers(store, dataset, conditions)
    for sample_id in ("q1", "q2", "q3"):
        no_rerank = results[(sample_id, "no_rerank")]
        rerank = results[(sample_id, "rerank")]
        assert [c["chunk_id"] for c in no_rerank["candidates"]] == [c["chunk_id"] for c in rerank["candidates"]]
        assert list(no_rerank["final_chunk_ids"]) == [c["chunk_id"] for c in no_rerank["candidates"][:2]]
        assert list(rerank["final_chunk_ids"]) == list(reversed([c["chunk_id"] for c in rerank["candidates"]]))[:2]
        assert len(no_rerank["final_chunk_ids"]) <= 2 and len(rerank["final_chunk_ids"]) <= 2
        assert rerank["rerank_status"] == "succeeded"
        assert no_rerank["rerank_status"] == "not_configured"
        assert results[(sample_id, "no_retrieval")]["retrieval_status"] == "not_requested"
        assert results[(sample_id, "no_retrieval")]["final_chunk_ids"] == []

    # Isolation: every prompt is exactly system+user and no earlier question leaks in.
    for index, messages in enumerate(generation_calls):
        assert [m["role"] for m in messages] == ["system", "user"]
    later_prompts = [m for messages in generation_calls for m in messages if "question 2" in m["content"]]
    assert later_prompts, "second question must be present"
    for messages in generation_calls:
        joined = json.dumps(messages)
        if "question 2" in joined:
            assert "question 1" not in joined, "earlier question leaked into a later prompt"


def test_no_retrieval_skips_vector_path_and_uses_baseline_prompt(tmp_path):
    dataset = make_dataset(items=2)
    store = make_store(tmp_path)
    generation_calls = []

    def retrieve(question):  # pragma: no cover - must not be called
        raise AssertionError("no_retrieval must not retrieve")

    outcome = run_collection(
        dataset, make_settings(), store, conditions=("no_retrieval",),
        retrieve=retrieve, generate=success_generator(generation_calls),
        grounded_system_prompt="ignored for baseline",
    )
    assert outcome.completed == 2
    assert len(generation_calls) == 2
    assert "general knowledge" in generation_calls[0][0]["content"]
    results = answers(store, dataset, ("no_retrieval",))
    assert results[("q1", "no_retrieval")]["timings_ms"]  # generation timing still recorded
    assert "retrieval" not in results[("q1", "no_retrieval")]["timings_ms"]


def test_retrieval_failure_marks_conditions_failed_and_run_continues(tmp_path):
    dataset = make_dataset(items=2)
    store = make_store(tmp_path)
    generation_calls = []
    state = {"n": 0}

    def retrieve(question):
        state["n"] += 1
        if state["n"] == 1:
            raise RetrievalFailure("vector_store_failed")
        return [record("cX", 1)], None

    outcome = run_collection(
        dataset, make_settings(), store, conditions=("no_rerank", "rerank"),
        retrieve=retrieve, rerank=lambda q, cs: RerankOrder(tuple(c.chunk_id for c in cs)),
        generate=success_generator(generation_calls),
        grounded_system_prompt="Use evidence.",
    )
    assert len(generation_calls) == 2, "only the second item may generate"
    assert outcome.by_condition["no_rerank"]["failed"] == 1
    assert outcome.by_condition["rerank"]["failed"] == 1
    codes = {failure["error"] for failure in outcome.failures}
    assert codes == {"vector_store_failed"}
    q1 = answers(store, dataset, ("no_rerank",))[("q1", "no_rerank")]
    assert q1 is None  # failed steps carry no result
    q2 = answers(store, dataset, ("no_rerank",))[("q2", "no_rerank")]
    assert q2["version_check"] == "unchecked" and q2["outcome"] == "completed"


def test_provider_failure_and_budget_block_are_distinct(tmp_path):
    dataset = make_dataset(items=2)
    store = make_store(tmp_path)
    calls = {"n": 0}

    def generate(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            raise GenerationFailure("provider_rate_limited")
        raise BudgetError("cap would be exceeded")

    outcome = run_collection(
        dataset, make_settings(), store, conditions=("no_retrieval",),
        generate=generate, grounded_system_prompt="prompt",
    )
    assert outcome.failed == 2 and outcome.completed == 0
    codes = [failure["error"] for failure in outcome.failures]
    assert sorted(codes) == ["budget_blocked", "provider_rate_limited"]

    # Even an explicit retry run must not re-dispatch a blocked ledger step.
    retry_calls = {"n": 0}

    def generate_after(messages):
        retry_calls["n"] += 1
        return GenerationOutcome(text="late", model_id="gemini-3.5-flash")

    outcome2 = run_collection(
        dataset, make_settings(), store, conditions=("no_retrieval",),
        generate=generate_after, grounded_system_prompt="prompt", retry_failed=True,
    )
    assert retry_calls["n"] == 1, "only the non-budget failure may be retried"
    assert outcome2.completed == 1 and outcome2.skipped_failed == 1


def test_resume_skips_completed_and_can_retry_failures(tmp_path):
    dataset = make_dataset(items=2)
    store = make_store(tmp_path)
    first_run_calls = {"n": 0}

    def flaky(messages):
        first_run_calls["n"] += 1
        if first_run_calls["n"] == 1:
            raise GenerationFailure("generation_error")
        return GenerationOutcome(text="ok", model_id="gemini-3.5-flash")

    first = run_collection(dataset, make_settings(), store, conditions=("no_retrieval",),
                           generate=flaky, grounded_system_prompt="prompt")
    assert first.completed == 1 and first.failed == 1

    second_calls = []

    def other(messages):
        second_calls.append(messages)
        return GenerationOutcome(text="resumed", model_id="gemini-3.5-flash")

    second = run_collection(dataset, make_settings(), store, conditions=("no_retrieval",),
                            generate=other, grounded_system_prompt="prompt")
    assert second.reused_completed == 1 and second.skipped_failed == 1 and not second_calls

    third = run_collection(dataset, make_settings(), store, conditions=("no_retrieval",),
                           generate=other, grounded_system_prompt="prompt", retry_failed=True)
    assert len(second_calls) == 1, "exactly the recorded failure is retried"
    assert third.completed == 2 and third.reused_completed == 1


def test_cancellation_checkpoints_and_resume_finishes(tmp_path):
    dataset = make_dataset(items=3)
    store = make_store(tmp_path)
    state = {"stop": False}
    calls = {"n": 0}

    def generate(messages):
        calls["n"] += 1
        if calls["n"] == 2:
            state["stop"] = True
        return GenerationOutcome(text="partial", model_id="gemini-3.5-flash")

    outcome = run_collection(dataset, make_settings(), store, conditions=("no_retrieval",),
                             generate=generate,
                             grounded_system_prompt="prompt", should_stop=lambda: state["stop"])
    assert outcome.cancelled is True
    assert outcome.completed == 2 and outcome.missing == 1
    assert not outcome.interrupted, "no step may be left running after cancellation"

    resume_calls = []

    def resume(messages):
        resume_calls.append(messages)
        return GenerationOutcome(text="done", model_id="gemini-3.5-flash")

    final = run_collection(dataset, make_settings(), store, conditions=("no_retrieval",),
                           generate=resume, grounded_system_prompt="prompt")
    assert final.completed == 3 and final.reused_completed == 2 and len(resume_calls) == 1


def test_rerank_failure_is_recorded_and_falls_back_visibly(tmp_path):
    dataset = make_dataset(items=1)
    store = make_store(tmp_path)
    calls = []

    def retrieve(question):
        return [record(f"c{i}", i) for i in range(1, 4)], {"s1": "v1"}

    def broken_rerank(question, candidates):
        raise RuntimeError("provider unavailable")

    run_collection(dataset, make_settings(), store, conditions=("no_rerank", "rerank"),
                   retrieve=retrieve, rerank=broken_rerank,
                   generate=success_generator(calls), grounded_system_prompt="prompt")
    results = answers(store, dataset, ("no_rerank", "rerank"))
    failed = results[("q1", "rerank")]
    assert failed["rerank_status"] == "failed"
    assert failed["final_chunk_ids"] == results[("q1", "no_rerank")]["final_chunk_ids"]
    assert failed["outcome"] == "completed"


def test_empty_retrieval_is_completed_not_an_error(tmp_path):
    dataset = make_dataset(items=1)
    store = make_store(tmp_path)
    calls = []
    run_collection(dataset, make_settings(), store, conditions=("no_rerank",),
                   retrieve=lambda question: ([], {"s1": "v1"}),
                   generate=success_generator(calls), grounded_system_prompt="prompt")
    result = answers(store, dataset, ("no_rerank",))[("q1", "no_rerank")]
    assert result["retrieval_status"] == "empty"
    assert result["exact_context"] == "" and len(calls) == 1


def test_stale_or_out_of_scope_candidates_are_rejected(tmp_path):
    dataset = make_dataset(items=1)
    store = make_store(tmp_path)

    def stale_retrieve(question):
        return [record("c1", 1, version="old")], {"s1": "v1"}

    run_collection(dataset, make_settings(), store, conditions=("no_rerank",),
                   retrieve=stale_retrieve, generate=success_generator([]),
                   grounded_system_prompt="prompt")
    record_ = store.get_step(StepKey("answer", "CSC1:q1", "no_rerank"))
    assert record_.status == "failed"
    assert any(f["error"] == "retrieval_validation_failed" for f in
               materialize_failures(store, dataset))

    # A candidate from a source outside the frozen manifest fails the same way.
    outside = SimpleNamespace(chunk=SimpleNamespace(id="cX", source_id="s9", source_version_id="v1",
                                                    text="outside text", token_count=3,
                                                    start_page=1, start_slide=None),
                              source=SimpleNamespace(id="s9"), score=0.5, rank_before=1, rank_after=1)
    store2 = make_store(tmp_path, name="runs2")
    run_collection(dataset, make_settings(), store2, conditions=("no_rerank",),
                   retrieve=lambda question: ([outside], None), generate=success_generator([]),
                   grounded_system_prompt="prompt")
    assert any(f["error"] == "candidate_outside_frozen_sources" for f in
               materialize_failures(store2, dataset))


def materialize_failures(store, dataset):
    from evaluation.program.runner import summarize_run
    return summarize_run(store, dataset, ("no_rerank",)).failures


def test_unanswerable_items_collect_without_evidence(tmp_path):
    dataset = make_dataset(items=1, include_unanswerable=True)
    store = make_store(tmp_path)
    calls = []
    outcome = run_collection(dataset, make_settings(), store, conditions=("reviewed_evidence",),
                             generate=success_generator(calls), grounded_system_prompt="prompt")
    assert outcome.completed == 2
    result = answers(store, dataset, ("reviewed_evidence",))[("q-unanswerable", "reviewed_evidence")]
    assert result["answerable"] is False and result["exact_context"] == ""


def test_workers_parallelise_without_duplicate_steps(tmp_path):
    dataset = make_dataset(items=4)
    store = make_store(tmp_path)
    retrieval_calls, generation_calls = [], []

    def retrieve(question):
        retrieval_calls.append(question)
        return [record("c1", 1)], {"s1": "v1"}

    outcome = run_collection(dataset, make_settings(), store, conditions=("no_rerank",),
                             retrieve=retrieve, generate=success_generator(generation_calls),
                             grounded_system_prompt="prompt", workers=4)
    assert outcome.completed == 8 and outcome.failed == 0  # 4 retrievals + 4 answers
    assert outcome.by_condition["no_rerank"]["completed"] == 4
    assert len(retrieval_calls) == 4 and len(generation_calls) == 4


def test_materialization_publishes_reconciled_views_once(tmp_path):
    dataset = make_dataset(items=2)
    store = make_store(tmp_path)
    run_collection(dataset, make_settings(), store, conditions=("no_rerank",),
                   retrieve=lambda question: ([record("c1", 1)], {"s1": "v1"}),
                   generate=success_generator([]), grounded_system_prompt="prompt")
    written = materialize_run(store, dataset, conditions=("no_rerank",))
    assert set(written) == {"answers.jsonl", "retrievals.jsonl", "failures.json", "summary.json"}
    summary = json.loads((store.path / "artifacts" / "summary.json").read_text(encoding="utf-8"))
    assert summary["completed"] == summary["expected_steps"] == 4
    assert summary["failed"] == 0 and summary["missing"] == 0
    # Idempotent while unchanged; refusing divergent rewrites keeps history intact.
    assert materialize_run(store, dataset, conditions=("no_rerank",))
    (store.path / "artifacts" / "answers.jsonl").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RunnerError, match="different content"):
        materialize_run(store, dataset, conditions=("no_rerank",))
