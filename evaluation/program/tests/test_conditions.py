import pytest

from evaluation.program.conditions import Candidate, build_context, matched_contexts, no_retrieval_context


def _candidates():
    return [
        Candidate("a", "s1", "First passage", 3, 0.9, 1),
        Candidate("b", "s1", "Second passage", 4, 0.8, 2),
        Candidate("c", "s2", "Third passage", 2, 0.7, 3),
    ]


def test_matched_conditions_use_same_candidates_and_context_budget():
    before, after = matched_contexts(_candidates(), final_k=2, max_context_tokens=5,
                                     rerank=lambda _: ["c", "a", "b"])
    assert before.raw_candidate_ids == after.raw_candidate_ids == ("a", "b", "c")
    assert before.final_chunk_ids == ("a", "c")
    assert after.final_chunk_ids == ("c", "a")
    assert "chunk:c" in after.exact_context and "Third passage" in after.exact_context
    assert after.omitted_chunk_ids == ("b",)


def test_invalid_rerank_is_failure_not_silent_fallback():
    with pytest.raises(ValueError, match="each original candidate"):
        matched_contexts(_candidates(), final_k=2, max_context_tokens=10,
                         rerank=lambda _: ["a", "a", "b"])


def test_empty_retrieval_is_valid_and_noretrieval_has_no_context():
    empty = build_context("no_rerank", [], [], final_k=5, max_context_tokens=100,
                          rerank_status="disabled")
    assert empty.final_chunk_ids == () and empty.exact_context == ""
    assert no_retrieval_context().raw_candidate_ids == ()
