"""Tests for the run report: reconciliation, quantiles, cost and bundle."""

from __future__ import annotations

from decimal import Decimal
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from evaluation.program.artifacts import RunStore
from evaluation.program.budget import BudgetLedger
from evaluation.program.config import EvaluationSettings
from evaluation.program.contracts import CourseDataset
from evaluation.program.reports.run_report import build_report, quantiles, render_markdown, reproducibility_bundle
from evaluation.program.runner import GenerationOutcome, run_collection
from evaluation.program.scoring import citation_checks_for_run, score_retrieval_for_run
import json

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


def _make_run(tmp_path: Path) -> tuple[RunStore, CourseDataset]:
    store = RunStore.create(tmp_path / "runs", configuration_hash=HASH_A, dataset_hash="b" * 64)
    dataset = make_dataset()
    outcome = run_collection(
        dataset, make_settings(), store, conditions=("no_rerank",),
        retrieve=lambda q: ([_record("c11", 1), _record("c12", 2)], {"s1": "v1"}),
        generate=lambda messages: GenerationOutcome(
            text="Answer [chunk:c11].", model_id="gemini-3.5-flash",
            usage={"promptTokenCount": 10, "candidatesTokenCount": 4}),
        grounded_system_prompt="Use evidence.",
    )
    assert outcome.failed == 0
    from evaluation.program.runner import materialize_run

    materialize_run(store, dataset, conditions=("no_rerank",))
    return store, dataset


def test_report_reconciles_counts_metrics_latency_and_cost(tmp_path):
    store, dataset = _make_run(tmp_path)
    rows = score_retrieval_for_run(dataset, store, conditions=("no_rerank",), k_values=(1,))
    Path(store.path / "artifacts" / "scoring").mkdir(parents=True)
    (store.path / "artifacts" / "scoring" / "retrieval.json").write_text(
        json.dumps([row.__dict__ for row in rows]), encoding="utf-8")
    citations = citation_checks_for_run(dataset, store, conditions=("no_rerank",))
    (store.path / "artifacts" / "scoring" / "citations.json").write_text(
        json.dumps([row.__dict__ for row in citations]), encoding="utf-8")

    ledger = BudgetLedger(store.path / "budget.sqlite3", Decimal("10"))
    from evaluation.program.config import configuration_hash
    config = configuration_hash(make_settings().public_snapshot())
    ledger.reserve("c1", "answer", Decimal("0.01"))
    ledger.settle("c1", Decimal("0.004"))

    report = build_report(dataset, store, conditions=("no_rerank",))
    assert report["reconciliation"]["completed"] == 2  # 1 retrieval + 1 answer
    assert report["per_condition"]["no_rerank"]["completed"] == 1
    assert report["latency_ms"]["generation"]["n"] == 1
    assert report["latency_ms"]["generation"]["p50"] is None  # below the minimum sample count
    assert report["tokens"]["input_known"] == 10 and report["tokens"]["output_known"] == 4
    assert report["retrieval_metrics"]["no_rerank:id_recall_at_k@1"]["mean"] == 1.0
    assert report["citation_status_counts"] == {"valid_target": 1}
    # The run-level ledger, not this report's test ledger, is read from the run directory.
    assert report["cost"]["present"] is True

    markdown = render_markdown(report)
    assert "# Evaluation run report: CSC1" in markdown
    assert "| no_rerank |" in markdown
    assert "retrieval_metrics" not in markdown  # raw keys stay JSON-side


def test_quantiles_require_minimum_samples():
    assert quantiles([1.0, 2.0], (0.5, 0.95)) == {"p50": None, "p95": None}
    assert quantiles([1.0, 2.0, 3.0, 4.0, 5.0], (0.5, 0.95, 0.99)) == {
        "p50": 3.0, "p95": 5.0, "p99": None,
    }
    values = [float(i) for i in range(1, 101)]
    result = quantiles(values, (0.5, 0.95, 0.99))
    assert result["p50"] == 50.0 and result["p95"] == 95.0 and result["p99"] == 99.0


def test_reproducibility_bundle_lists_artifact_hashes(tmp_path):
    store, dataset = _make_run(tmp_path)
    bundle = reproducibility_bundle(store.path, git_info={"revision": "abc", "dirty_file_count": 3})
    assert bundle["run_id"] == store.manifest["run_id"]
    assert bundle["git"] == {"revision": "abc", "dirty_file_count": 3}
    assert any(name.startswith("artifacts/") for name in bundle["artifact_sha256"])
    assert all(len(digest) == 64 for digest in bundle["artifact_sha256"].values())
