"""Offline tests for the live collection wiring.

Construction registers real adapters but must not touch the network. Frozen
source verification is exercised with a fake session.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

API_ROOT = Path(__file__).resolve().parents[3] / "apps" / "api"
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(API_ROOT.parent.parent))

from evaluation.program.budget import BudgetLedger
from evaluation.program.config import EvaluationSettings
from evaluation.program.contracts import CourseDataset
from evaluation.program.live import build_live_collection, collection_preflight, verify_frozen_sources
from evaluation.program.pricing import PriceRegistry
from evaluation.program.runner import RunnerError

HASH_A = "a" * 64
FAKE_ENV = {
    "CURRICULUM_TUTOR_GEMINI_API_KEY": "fake-gemini",
    "CURRICULUM_TUTOR_OPENROUTER_API_KEY": "fake-openrouter",
}


def make_settings(**overrides) -> EvaluationSettings:
    values = dict(
        answer_model="gemini-3.5-flash",
        author_model="moonshotai/kimi-k2.6",
        judge_model="deepseek/deepseek-v4.1-flash",
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-large",
        relevance_embedding_provider="openrouter",
        relevance_embedding_model="openai/text-embedding-3-large",
        reranker_model="openai/gpt-4.1-mini",
        max_api_spend_usd=10,
    )
    values.update(overrides)
    return EvaluationSettings(**values)


def make_dataset() -> CourseDataset:
    return CourseDataset(
        dataset_id="csc1-v1", version="1", course_id="CSC1", notebook_id="n1",
        source_manifest=[{"source_id": "s1", "source_label": "Lecture", "notebook_id": "n1", "sha256": HASH_A}],
        samples=[{"sample_id": "q1", "question": "What is the answer?",
                  "reference_answer": "A", "evidence": [{"source_id": "s1", "chunk_id": "c1", "quote": "A"}]}],
    )


def test_collection_preflight_names_missing_credentials_and_unsupported_embeddings():
    issues = collection_preflight(make_settings(), need_retrieval=True, need_rerank=False, environ={})
    assert any("CURRICULUM_TUTOR_GEMINI_API_KEY" in issue for issue in issues)
    assert any("CURRICULUM_TUTOR_OPENROUTER_API_KEY" in issue for issue in issues)
    assert all("fake" not in issue for issue in issues)

    gemini = make_settings(embedding_provider="gemini", embedding_model="text-embedding-004")
    issues = collection_preflight(gemini, need_retrieval=True, need_rerank=False, environ=FAKE_ENV)
    assert any("OpenRouter" in issue for issue in issues)

    issues = collection_preflight(make_settings(), need_retrieval=True, need_rerank=False, environ=FAKE_ENV)
    assert issues == []


def test_verify_frozen_sources_accepts_matching_and_rejects_drift():
    from app.models.enums import SourceStatus

    def session_for(source):
        class Query:
            def filter(self, *args):
                return self

            def one_or_none(self):
                return SimpleNamespace(id="ver-1", checksum_sha256=source.checksum_sha256,
                                       status=SourceStatus.INDEXED.value)

        return SimpleNamespace(get=lambda model, key: source, query=lambda model: Query())

    source = SimpleNamespace(id="s1", deleted_at=None, status=SourceStatus.INDEXED, notebook_id="n1",
                             checksum_sha256=HASH_A, version_number=1)
    assert verify_frozen_sources(session_for(source), make_dataset()) == {"s1": "ver-1"}

    changed = SimpleNamespace(**{**vars(source), "checksum_sha256": "b" * 64})
    with pytest.raises(RunnerError, match="checksum changed"):
        verify_frozen_sources(session_for(changed), make_dataset())

    deleted = SimpleNamespace(**{**vars(source), "deleted_at": "2026-01-01"})
    with pytest.raises(RunnerError, match="not currently indexed"):
        verify_frozen_sources(session_for(deleted), make_dataset())

    missing = SimpleNamespace(get=lambda model, key: None, query=lambda model: None)
    with pytest.raises(RunnerError, match="not currently indexed"):
        verify_frozen_sources(missing, make_dataset())


def test_build_live_collection_registers_callables_without_network(tmp_path):
    settings = make_settings()
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", Decimal("10"))
    runtime = build_live_collection(
        settings, make_dataset(), ledger=ledger,
        prices=PriceRegistry(version="1.0", schedules=()),
        environ=FAKE_ENV, verify_versions=False, need_retrieval=True, need_rerank=True,
    )
    assert callable(runtime.retrieve) and callable(runtime.rerank) and callable(runtime.generate)
    assert runtime.grounded_system_prompt.strip()
    runtime.close()


def test_build_refuses_ledger_cap_mismatch(tmp_path):
    settings = make_settings(max_api_spend_usd=5)
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", Decimal("10"))
    with pytest.raises(RunnerError, match="USD cap"):
        build_live_collection(settings, make_dataset(), ledger=ledger,
                              prices=PriceRegistry(version="1.0", schedules=()),
                              environ=FAKE_ENV, verify_versions=False)
