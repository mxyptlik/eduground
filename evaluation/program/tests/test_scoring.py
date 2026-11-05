"""Offline tests for journal-based scoring: retrieval, citations and RAGAS wiring."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from evaluation.program.artifacts import RunStore
from evaluation.program.config import EvaluationSettings
from evaluation.program.contracts import CourseDataset
from evaluation.program.conditions import RerankOrder
from evaluation.program.metrics import ragas as ragas_module
from evaluation.program.runner import GenerationOutcome, run_collection
from evaluation.program.scoring import (
    citation_checks_for_run,
    score_ragas_for_run,
    score_retrieval_for_run,
)

HASH_A = "a" * 64


def make_settings() -> EvaluationSettings:
    return EvaluationSettings(
        answer_model="gemini-3.5-flash", author_model="moonshotai/kimi-k2.6",
        judge_model="deepseek/deepseek-v4.1-flash",
        embedding_provider="openrouter", embedding_model="openai/text-embedding-3-large",
        relevance_embedding_provider="openrouter", relevance_embedding_model="openai/text-embedding-3-large",
        candidate_k=4, final_k=2, max_context_tokens=128, max_api_spend_usd=10,
    )


def make_dataset() -> CourseDataset:
    return CourseDataset(
        dataset_id="csc1-v1", version="1", course_id="CSC1", notebook_id="n1",
        source_manifest=[{"source_id": "s1", "source_label": "Lecture", "notebook_id": "n1", "sha256": HASH_A}],
        samples=[{"sample_id": "q1", "question": "What is the answer?",
                  "reference_answer": "The answer is X.",
                  "evidence": [{"source_id": "s1", "chunk_id": "c11", "quote": "quote", "locator": "page 1"}]}],
    )


def _record(chunk_id: str, rank: int) -> SimpleNamespace:
    chunk = SimpleNamespace(id=chunk_id, source_id="s1", source_version_id="v1",
                            text=f"Evidence {chunk_id}", token_count=5, start_page=1, start_slide=None)
    return SimpleNamespace(chunk=chunk, source=SimpleNamespace(id="s1"), score=1.0 / rank,
                           rank_before=rank, rank_after=rank, rerank_score=None)


def _run(store: RunStore, *, answer_text: str, conditions=("no_rerank", "rerank")):
    def retrieve(question):
        return [_record("c11", 1), _record("c12", 2), _record("c13", 3)], {"s1": "v1"}

    def rerank(question, candidates):
        return RerankOrder(tuple(reversed([c.chunk_id for c in candidates])), "succeeded",
                           scores=(0.9, 0.5, 0.1))

    calls = []

    def generate(messages):
        calls.append(messages)
        return GenerationOutcome(text=answer_text, model_id="gemini-3.5-flash")

    outcome = run_collection(make_dataset(), make_settings(), store, conditions=conditions,
                             retrieve=retrieve, rerank=rerank, generate=generate,
                             grounded_system_prompt="Use evidence and cite [chunk:id].")
    assert outcome.failed == 0
    return calls


def test_retrieval_metrics_use_effective_ranking_and_minimum_labels(tmp_path):
    store = RunStore.create(tmp_path / "runs", configuration_hash=HASH_A, dataset_hash="b" * 64)
    _run(store, answer_text="Answer.")
    rows = score_retrieval_for_run(make_dataset(), store, conditions=("no_rerank", "rerank"), k_values=(1, 3))
    by_key = {(row.condition, row.metric_id, row.k): row for row in rows}
    assert all(row.relevance_source == "frozen_evidence_minimum" for row in rows)
    # no_rerank keeps original order: relevant c11 first.
    assert by_key[("no_rerank", "mrr", 3)].value == pytest.approx(1.0)
    assert by_key[("no_rerank", "id_recall_at_k", 1)].value == pytest.approx(1.0)
    # rerank reverses the pool: c11 now ranks third, still recalled at k=3.
    assert by_key[("rerank", "mrr", 3)].value == pytest.approx(1 / 3)
    assert by_key[("rerank", "id_recall_at_k", 1)].value == pytest.approx(0.0)
    assert by_key[("rerank", "id_recall_at_k", 3)].value == pytest.approx(1.0)


def test_retrieval_scoring_skips_non_retrieval_conditions(tmp_path):
    store = RunStore.create(tmp_path / "runs", configuration_hash=HASH_A, dataset_hash="b" * 64)
    _run(store, answer_text="Answer.", conditions=("no_retrieval",))
    rows = score_retrieval_for_run(make_dataset(), store, conditions=("no_retrieval",), k_values=(1,))
    assert rows == []


def test_citation_checks_only_count_observed_markers(tmp_path):
    store = RunStore.create(tmp_path / "runs", configuration_hash=HASH_A, dataset_hash="b" * 64)
    # no_rerank final context = [c11, c12]; c13 is a candidate but not in context.
    _run(store, answer_text="First fact [chunk:c12]. Second [chunk:c13]. Third [chunk:c99]. Plain fact.")
    rows = citation_checks_for_run(make_dataset(), store, conditions=("no_rerank",))
    statuses = {row.marker: row.status for row in rows}
    assert statuses["[chunk:c12]"] == "valid_target"
    assert statuses["[chunk:c13]"] == "out_of_context"
    assert statuses["[chunk:c99]"] == "missing_target"
    # Plain facts without markers never become links.
    assert all(row.marker.startswith("[chunk:") for row in rows)


def test_ragas_scoring_marks_na_without_judge_and_journals_resume(tmp_path, monkeypatch):
    store = RunStore.create(tmp_path / "runs", configuration_hash=HASH_A, dataset_hash="b" * 64)
    _run(store, answer_text="Answer [chunk:c11].", conditions=("no_rerank", "no_retrieval"))
    seen = []

    async def fake_metric(metric_id, **kwargs):
        seen.append((metric_id, kwargs))
        return ragas_module.RagasOutcome(metric_id, 0.5, "completed", None, "0.4.3")

    monkeypatch.setattr(ragas_module, "score_ragas_metric", fake_metric)
    rows = score_ragas_for_run(make_dataset(), store, conditions=("no_rerank", "no_retrieval"),
                               metric_ids=["faithfulness"], judge_llm=object())
    by_condition = {row.condition: row for row in rows}
    assert by_condition["no_retrieval"].status == "not_applicable"
    assert by_condition["no_rerank"].value == 0.5
    assert len(seen) == 1, "no judge call for the N/A no-retrieval condition"
    metric_id, kwargs = seen[0]
    assert kwargs["retrieved_contexts"] == ["Evidence c11", "Evidence c12"]
    assert kwargs["reference"] == "The answer is X."

    rows2 = score_ragas_for_run(make_dataset(), store, conditions=("no_rerank", "no_retrieval"),
                                metric_ids=["faithfulness"], judge_llm=object())
    assert len(seen) == 1, "completed metric steps resume without a new judge call"
    assert {(row.condition, row.status) for row in rows2} == {("no_rerank", "completed"),
                                                             ("no_retrieval", "not_applicable")}


def test_ragas_limit_defers_without_calling(tmp_path, monkeypatch):
    store = RunStore.create(tmp_path / "runs", configuration_hash=HASH_A, dataset_hash="b" * 64)
    _run(store, answer_text="Answer [chunk:c11].", conditions=("no_rerank",))
    calls = {"n": 0}

    async def fake_metric(metric_id, **kwargs):
        calls["n"] += 1
        return ragas_module.RagasOutcome(metric_id, 1.0, "completed", None, "0.4.3")

    monkeypatch.setattr(ragas_module, "score_ragas_metric", fake_metric)
    rows = score_ragas_for_run(make_dataset(), store, conditions=("no_rerank",),
                               metric_ids=["faithfulness"], judge_llm=object(), limit=0)
    assert calls["n"] == 0
    assert rows[0].status == "deferred_limit"


def test_failed_judge_steps_retry_only_when_explicit(tmp_path, monkeypatch):
    store = RunStore.create(tmp_path / "runs", configuration_hash=HASH_A, dataset_hash="b" * 64)
    _run(store, answer_text="Answer [chunk:c11].", conditions=("no_rerank",))
    calls = {"n": 0}

    async def flaky(metric_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("judge exploded")
        return ragas_module.RagasOutcome(metric_id, 0.9, "completed", None, "0.4.3")

    monkeypatch.setattr(ragas_module, "score_ragas_metric", flaky)
    first = score_ragas_for_run(make_dataset(), store, conditions=("no_rerank",),
                                metric_ids=["faithfulness"], judge_llm=object())
    assert first[0].status == "failed" and calls["n"] == 1

    second = score_ragas_for_run(make_dataset(), store, conditions=("no_rerank",),
                                 metric_ids=["faithfulness"], judge_llm=object())
    assert second[0].status == "failed" and calls["n"] == 1, "no silent retry"

    third = score_ragas_for_run(make_dataset(), store, conditions=("no_rerank",),
                                metric_ids=["faithfulness"], judge_llm=object(), retry_failed=True)
    assert third[0].status == "completed" and third[0].value == 0.9 and calls["n"] == 2


def test_missing_answers_are_explicit_rows_not_dropped(tmp_path):
    store = RunStore.create(tmp_path / "runs", configuration_hash=HASH_A, dataset_hash="b" * 64)
    rows = score_ragas_for_run(make_dataset(), store, conditions=("no_rerank",),
                               metric_ids=["faithfulness"], judge_llm=object())
    assert rows[0].status == "no_answer"
    assert rows[0].value is None
