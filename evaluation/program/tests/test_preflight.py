from __future__ import annotations

import json

import pytest

from evaluation.program.config import EvaluationSettings
from evaluation.program.metric_registry import score_selected, select_metrics
from evaluation.program.preflight import plan_evaluation


def _dataset(path):
    path.write_text(json.dumps({
        "schema_version": 2, "dataset_id": "d1", "version": "1", "course_id": "c1",
        "notebook_id": "n1", "source_manifest": [{"source_id": "s1", "source_label": "S",
            "notebook_id": "n1", "sha256": "a" * 64}],
        "samples": [{"sample_id": "q1", "question": "What is the answer?", "reference_answer": "A",
            "evidence": [{"source_id": "s1", "chunk_id": "k1", "quote": "A"}], "integrity": "verified"}],
    }), encoding="utf-8")


def test_only_implemented_metric_is_selected_without_provider_import():
    assert [spec.metric_id for spec in select_metrics(["id_recall_at_k"])] == ["id_recall_at_k"]
    assert select_metrics(["faithfulness"])[0].module.endswith("ragas")
    with pytest.raises(ValueError, match="not implemented"):
        select_metrics(["citation_sufficiency"])
    assert score_selected(["id_recall_at_k"], ["k1"], k=1, relevant_ids=["k1"])["id_recall_at_k"]["value"] == 1


def test_plan_is_read_only_and_surfaces_missing_deps(tmp_path):
    path = tmp_path / "course.json"
    _dataset(path)
    settings = EvaluationSettings(_env_file=None, max_api_spend_usd=10)
    before = set(tmp_path.iterdir())
    plan = plan_evaluation([path], metric_ids=["id_recall_at_k"], conditions=["rerank", "no_rerank"], settings=settings)
    assert set(tmp_path.iterdir()) == before
    assert plan["course_count"] == 1 and plan["item_count"] == 1
    assert plan["estimated_external_calls"]["answer_lower_bound"] == 2
    assert plan["estimated_cost_usd"] is None
    assert not plan["execution_ready"]
    assert any("embedding" in issue for issue in plan["issues"])


def test_rerank_condition_requires_pinned_reranker(tmp_path):
    path = tmp_path / "course.json"
    _dataset(path)
    settings = EvaluationSettings(_env_file=None, max_api_spend_usd=10,
        embedding_provider="gemini", embedding_model="text-embedding-004")
    plan = plan_evaluation([path], metric_ids=["id_recall_at_k"], conditions=["rerank"], settings=settings)
    assert not plan["input_checks_passed"]
    assert any("reranker" in issue for issue in plan["issues"])


def test_pinned_configuration_is_execution_ready_with_lower_bounds(tmp_path):
    path = tmp_path / "course.json"
    _dataset(path)
    settings = EvaluationSettings(_env_file=None, max_api_spend_usd=10,
        embedding_provider="gemini", embedding_model="text-embedding-004",
        reranker_model="openai/gpt-4.1-mini")
    plan = plan_evaluation([path], metric_ids=["faithfulness"], conditions=["no_rerank", "rerank"], settings=settings)
    assert plan["input_checks_passed"] and plan["execution_ready"]
    assert plan["estimated_external_calls"]["answer_lower_bound"] == 2
    assert plan["estimated_external_calls"]["judge_lower_bound"] == 2
    assert plan["estimated_external_calls"]["reranker_lower_bound"] == 1
    assert plan["collector"].startswith("evaluation.program.runner")


def test_relevancy_embedding_only_required_for_relevancy_metric(tmp_path):
    path = tmp_path / "course.json"
    _dataset(path)
    settings = EvaluationSettings(_env_file=None, max_api_spend_usd=10,
        embedding_provider="gemini", embedding_model="text-embedding-004")
    plan = plan_evaluation([path], metric_ids=["answer_relevancy"], conditions=["rerank"], settings=settings)
    assert any("answer-relevancy embedding" in issue for issue in plan["issues"])


def test_no_retrieval_metric_marked_inapplicable(tmp_path):
    path = tmp_path / "course.json"
    _dataset(path)
    settings = EvaluationSettings(_env_file=None, max_api_spend_usd=10,
        embedding_provider="gemini", embedding_model="text-embedding-004",
        relevance_embedding_provider="gemini", relevance_embedding_model="text-embedding-004")
    plan = plan_evaluation([path], metric_ids=["id_recall_at_k"], conditions=["no_retrieval"], settings=settings)
    assert not plan["execution_ready"]
    assert any("N/A" in issue for issue in plan["issues"])
