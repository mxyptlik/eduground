"""Known-answer tests for reviewer-labelled retrieval metrics."""

from math import log2

import pytest

from evaluation.program.metrics.retrieval import hit_rate, mean_reciprocal_rank, score_retrieval


def test_ranked_evidence_scores_hand_calculated_values():
    scores = score_retrieval(
        ["noise", "support_b", "support_a"],
        k=3,
        relevant_ids=["support_a", "support_b"],
    )
    assert scores.recall_at_k == 1.0
    assert scores.precision_at_k == pytest.approx(2 / 3)
    assert scores.hit_at_k == 1.0
    assert scores.reciprocal_rank == 0.5
    expected_dcg = 1 / log2(3) + 1 / log2(4)
    ideal_dcg = 1 + 1 / log2(3)
    assert scores.ndcg_at_k == pytest.approx(expected_dcg / ideal_dcg)


def test_alternative_passages_satisfy_one_requirement_only():
    scores = score_retrieval(
        ["paragraph_a", "paragraph_b", "proof"],
        k=3,
        relevant_ids=["proof"],
        alternative_groups={"definition": ["paragraph_a", "paragraph_b"]},
    )
    assert scores.relevant_requirement_count == 2
    assert scores.recall_at_k == 1.0
    assert scores.precision_at_k == pytest.approx(2 / 3)
    assert scores.ndcg_at_k < 1.0


def test_duplicate_id_does_not_gain_credit_or_shift_actual_rank():
    scores = score_retrieval(["noise", "noise", "right"], k=3, relevant_ids=["right"])
    assert scores.recall_at_k == 1.0
    assert scores.precision_at_k == 0.5  # two unique IDs in the top three
    assert scores.reciprocal_rank == pytest.approx(1 / 3)
    assert scores.ndcg_at_k == pytest.approx(0.5)


def test_empty_and_shorter_than_k_use_observed_results():
    empty = score_retrieval([], k=5, relevant_ids=["right"])
    assert (empty.recall_at_k, empty.precision_at_k, empty.hit_at_k) == (0.0, 0.0, 0.0)
    assert (empty.reciprocal_rank, empty.ndcg_at_k, empty.retrieved_count) == (0.0, 0.0, 0)

    short = score_retrieval(["right"], k=5, relevant_ids=["right", "other"])
    assert short.recall_at_k == 0.5
    assert short.precision_at_k == 1.0
    assert short.hit_at_k == 1.0
    assert short.retrieved_count == 1


def test_grades_reward_stronger_evidence_earlier():
    ideal = score_retrieval(
        ["strong", "weak"], k=2, relevant_ids=["strong", "weak"],
        relevance_grades={"strong": 3, "weak": 1},
    )
    reversed_order = score_retrieval(
        ["weak", "strong"], k=2, relevant_ids=["strong", "weak"],
        relevance_grades={"strong": 3, "weak": 1},
    )
    assert ideal.ndcg_at_k == pytest.approx(1.0)
    assert reversed_order.ndcg_at_k < ideal.ndcg_at_k
    assert reversed_order.recall_at_k == ideal.recall_at_k == 1.0


def test_missing_labels_and_unanswerable_are_not_zero_scores():
    missing = score_retrieval(["somewhere"], k=1)
    unanswerable = score_retrieval([], k=1, relevant_ids=["right"], answerable=False)
    assert missing.recall_at_k is None
    assert missing.ndcg_at_k is None
    assert missing.reason == "no positive relevance labels"
    assert unanswerable.precision_at_k is None
    assert unanswerable.reason == "unanswerable"
    assert mean_reciprocal_rank([missing, unanswerable]) is None


def test_macro_mrr_excludes_na_but_includes_no_hit():
    scores = [
        score_retrieval(["right"], k=1, relevant_ids=["right"]),
        score_retrieval(["wrong"], k=1, relevant_ids=["right"]),
        score_retrieval([], k=1),
    ]
    assert mean_reciprocal_rank(scores) == 0.5
    assert hit_rate(scores) == 0.5


def test_no_applicable_questions_has_no_hit_rate():
    assert hit_rate([score_retrieval([], k=1)]) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"k": 0, "relevant_ids": ["a"]},
        {"k": 1, "relevant_ids": ["a", "a"]},
        {"k": 1, "alternative_groups": {"g": []}},
        {"k": 1, "relevant_ids": ["a"], "alternative_groups": {"g": ["a"]}},
        {"k": 1, "relevant_ids": ["a"], "relevance_grades": {"b": 2}},
        {"k": 1, "relevant_ids": ["a"], "relevance_grades": {"a": -1}},
    ],
)
def test_invalid_judgments_are_rejected(kwargs):
    with pytest.raises(ValueError):
        score_retrieval([], **kwargs)
