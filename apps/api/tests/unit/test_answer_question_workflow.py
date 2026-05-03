from __future__ import annotations

from types import SimpleNamespace

from app.integrations.vectorstore.base import VectorStoreError
from app.workflows.answer_question import AnswerQuestionWorkflow


def test_retrieve_candidate_chunks_returns_empty_when_vector_store_fails() -> None:
    workflow = AnswerQuestionWorkflow.__new__(AnswerQuestionWorkflow)
    workflow.embeddings = SimpleNamespace(embed=lambda _texts: [[0.1, 0.2, 0.3]])
    workflow.vector_store = SimpleNamespace(search=lambda *_args, **_kwargs: (_ for _ in ()).throw(VectorStoreError("boom")))
    workflow.db = None
    workflow.reranker = None

    results = workflow._retrieve_candidate_chunks({"notebook_id": "nb_123"}, "hello")

    assert results == []
