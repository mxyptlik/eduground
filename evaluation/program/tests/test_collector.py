from types import SimpleNamespace
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

from evaluation.program.collector import prompt_messages, rerank_order, snapshot_candidates
from evaluation.program.conditions import Candidate, ConditionContext, no_retrieval_context


def _record(chunk_id="c1", version="v1"):
    source = SimpleNamespace(id="s1")
    chunk = SimpleNamespace(id=chunk_id, source_id="s1", source_version_id=version,
                            text="Evidence text", token_count=4,
                            start_page=2, start_slide=None)
    return SimpleNamespace(chunk=chunk, source=source, score=0.8, rank_before=1, rank_after=1)


def test_snapshot_rejects_stale_evidence_and_preserves_locator():
    result = snapshot_candidates([_record()], current_source_versions={"s1": "v1"})
    assert result[0].locator == "page 2"
    assert result[0].text == "Evidence text"
    with pytest.raises(ValueError, match="frozen source version"):
        snapshot_candidates([_record(version="old")], current_source_versions={"s1": "v1"})


def test_partial_reranker_is_visible():
    class Reranker:
        def rerank(self, question, items):
            return items

    result = rerank_order("Question?", [_record()], Reranker())
    assert result.status == "partial" and result.chunk_ids == ("c1",)


def test_actual_reranker_model_must_match_pin():
    class Reranker:
        config = SimpleNamespace(model="pinned/model")

        def rerank_with_result(self, question, items, *, strict):
            assert strict
            return SimpleNamespace(items=items, response_model="different/model",
                                   request_id="r1", usage={})

    with pytest.raises(ValueError, match="pinned model"):
        rerank_order("Question?", [_record()], Reranker())


def test_no_retrieval_prompt_allows_prior_knowledge_and_grounded_prompt_has_exact_context():
    baseline = prompt_messages(no_retrieval_context(), question="What is LIFO?", grounded_system_prompt="ignored")
    assert "general knowledge" in baseline[0]["content"]
    assert "retrieved_evidence" not in str(baseline)
    condition = ConditionContext("rerank", ("c1",), ("c1",), "[source:s1 chunk:c1]\nEvidence text", (), "succeeded")
    grounded = prompt_messages(condition, question="What is LIFO?", grounded_system_prompt="Use evidence.")
    assert grounded[0]["role"] == "system"
    assert "Evidence text" in grounded[-1]["content"]
    assert "<retrieved_evidence>" in grounded[-1]["content"]
