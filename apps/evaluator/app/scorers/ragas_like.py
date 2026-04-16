from __future__ import annotations

from app.models import EvaluationScore, GoldenQASample


def _overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def score_sample(sample: GoldenQASample) -> EvaluationScore:
    answer_tokens = set(sample.model_answer.lower().split())
    expected_tokens = set(sample.expected_answer.lower().split())
    context_tokens = set(sample.retrieved_context.lower().split())
    faithfulness = _overlap_ratio(answer_tokens, context_tokens)
    recall = _overlap_ratio(expected_tokens, context_tokens)
    return EvaluationScore(
        sample_key=sample.sample_key,
        faithfulness_score=round(faithfulness, 4),
        context_recall_score=round(recall, 4),
        pass_fail=faithfulness >= 0.5 and recall >= 0.4,
    )

