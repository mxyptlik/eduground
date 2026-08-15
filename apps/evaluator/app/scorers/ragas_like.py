from __future__ import annotations

import re

from app.models import EvaluationScore, GoldenQASample


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", text.lower()))


def _coverage_ratio(target: set[str], evidence: set[str]) -> float:
    if not target or not evidence:
        return 0.0
    return len(target & evidence) / len(target)


def score_sample(sample: GoldenQASample) -> EvaluationScore:
    answer_tokens = _tokens(sample.model_answer)
    expected_tokens = _tokens(sample.expected_answer)
    context_tokens = _tokens(sample.retrieved_context)
    faithfulness = _coverage_ratio(answer_tokens, context_tokens)
    recall = _coverage_ratio(expected_tokens, context_tokens)
    return EvaluationScore(
        sample_key=sample.sample_key,
        faithfulness_score=round(faithfulness, 4),
        context_recall_score=round(recall, 4),
        pass_fail=faithfulness >= 0.5 and recall >= 0.4,
    )
