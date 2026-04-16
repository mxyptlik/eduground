from __future__ import annotations

from pydantic import BaseModel


class GoldenQASample(BaseModel):
    sample_key: str
    question: str
    expected_answer: str
    retrieved_context: str
    model_answer: str


class EvaluationScore(BaseModel):
    sample_key: str
    faithfulness_score: float
    context_recall_score: float
    pass_fail: bool

