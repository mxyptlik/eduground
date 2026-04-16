from app.models import GoldenQASample
from app.scorers.ragas_like import score_sample


def test_score_sample_returns_pass_for_grounded_overlap() -> None:
    sample = GoldenQASample(
        sample_key="sample",
        question="Q",
        expected_answer="cited grounded response",
        retrieved_context="this context contains cited grounded response",
        model_answer="grounded response",
    )
    result = score_sample(sample)
    assert result.faithfulness_score > 0
    assert result.context_recall_score > 0
