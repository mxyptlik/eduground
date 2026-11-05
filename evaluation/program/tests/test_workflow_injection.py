from types import SimpleNamespace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from app.models.content import Chunk, Source
from app.models.enums import SourceStatus
from app.workflows.answer_question import AnswerQuestionWorkflow


def test_candidate_depth_above_five_without_global_setting_mutation():
    seen = []

    class Vector:
        def search(self, vector, filters, *, top_k):
            seen.append(top_k)
            return [{"payload": {"chunk_id": f"c{i}", "source_id": "s1"}, "score": 1 / i}
                    for i in range(1, top_k + 1)]

    class DB:
        def get(self, model, key):
            if model is Chunk:
                return SimpleNamespace(id=key, text=f"passage {key}")
            if model is Source:
                return SimpleNamespace(id=key, status=SourceStatus.INDEXED)
            raise AssertionError(model)

    workflow = AnswerQuestionWorkflow(DB(), llm=object(), embeddings=SimpleNamespace(embed=lambda _: [[0.1]]),
                                      vector_store=Vector(), reranker=None, candidate_k=7)
    candidates = workflow._retrieve_candidate_chunks({"notebook_id": "n1"}, "What is X?")
    assert seen == [7]
    assert len(candidates) == 7
    assert [candidate.rank_before for candidate in candidates] == list(range(1, 8))
